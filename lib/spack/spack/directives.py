##############################################################################
# Copyright (c) 2013-2017, Lawrence Livermore National Security, LLC.
# Produced at the Lawrence Livermore National Laboratory.
#
# This file is part of Spack.
# Created by Todd Gamblin, tgamblin@llnl.gov, All rights reserved.
# LLNL-CODE-647188
#
# For details, see https://github.com/llnl/spack
# Please also see the NOTICE and LICENSE files for our notice and the LGPL.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License (as
# published by the Free Software Foundation) version 2.1, February 1999.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the IMPLIED WARRANTY OF
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the terms and
# conditions of the GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA
##############################################################################
"""This package contains directives that can be used within a package.

Directives are functions that can be called inside a package
definition to modify the package, for example:

    class OpenMpi(Package):
        depends_on("hwloc")
        provides("mpi")
        ...

``provides`` and ``depends_on`` are spack directives.

The available directives are:

  * ``version``
  * ``depends_on``
  * ``provides``
  * ``extends``
  * ``patch``
  * ``variant``
  * ``resource``

"""

import collections
import functools
import inspect
import os.path
import re
from six import string_types

import llnl.util.lang
from llnl.util.filesystem import join_path

import spack
import spack.error
import spack.spec
import spack.url
from spack.dependency import *
from spack.fetch_strategy import from_kwargs
from spack.patch import Patch
from spack.resource import Resource
from spack.spec import Spec, parse_anonymous_spec
from spack.variant import Variant
from spack.version import Version

__all__ = []

#: These are variant names used by Spack internally; packages can't use them
reserved_names = ['patches']


class DirectiveMetaMixin(type):
    """Flushes the directives that were temporarily stored in the staging
    area into the package.
    """

    # Set of all known directives
    _directive_names = set()
    _directives_to_be_executed = []

    def __new__(mcs, name, bases, attr_dict):
        # Initialize the attribute containing the list of directives
        # to be executed. Here we go reversed because we want to execute
        # commands:
        # 1. in the order they were defined
        # 2. following the MRO
        attr_dict['_directives_to_be_executed'] = []
        for base in reversed(bases):
            try:
                directive_from_base = base._directives_to_be_executed
                attr_dict['_directives_to_be_executed'].extend(
                    directive_from_base
                )
            except AttributeError:
                # The base class didn't have the required attribute.
                # Continue searching
                pass
        # De-duplicates directives from base classes
        attr_dict['_directives_to_be_executed'] = [
            x for x in llnl.util.lang.dedupe(
                attr_dict['_directives_to_be_executed']
            )
        ]

        # Move things to be executed from module scope (where they
        # are collected first) to class scope
        if DirectiveMetaMixin._directives_to_be_executed:
            attr_dict['_directives_to_be_executed'].extend(
                DirectiveMetaMixin._directives_to_be_executed
            )
            DirectiveMetaMixin._directives_to_be_executed = []

        return super(DirectiveMetaMixin, mcs).__new__(
            mcs, name, bases, attr_dict
        )

    def __init__(cls, name, bases, attr_dict):
        # The class is being created: if it is a package we must ensure
        # that the directives are called on the class to set it up
        module = inspect.getmodule(cls)
        if 'spack.pkg' in module.__name__:
            # Package name as taken
            # from llnl.util.lang.get_calling_module_name
            pkg_name = module.__name__.split('.')[-1]
            setattr(cls, 'name', pkg_name)
            # Ensure the presence of the dictionaries associated
            # with the directives
            for d in DirectiveMetaMixin._directive_names:
                setattr(cls, d, {})
            # Lazy execution of directives
            for directive in cls._directives_to_be_executed:
                directive(cls)

        super(DirectiveMetaMixin, cls).__init__(name, bases, attr_dict)

    @staticmethod
    def directive(dicts=None):
        """Decorator for Spack directives.

        Spack directives allow you to modify a package while it is being
        defined, e.g. to add version or dependency information.  Directives
        are one of the key pieces of Spack's package "language", which is
        embedded in python.

        Here's an example directive:

            @directive(dicts='versions')
            version(pkg, ...):
                ...

        This directive allows you write:

            class Foo(Package):
                version(...)

        The ``@directive`` decorator handles a couple things for you:

          1. Adds the class scope (pkg) as an initial parameter when
             called, like a class method would.  This allows you to modify
             a package from within a directive, while the package is still
             being defined.

          2. It automatically adds a dictionary called "versions" to the
             package so that you can refer to pkg.versions.

        The ``(dicts='versions')`` part ensures that ALL packages in Spack
        will have a ``versions`` attribute after they're constructed, and
        that if no directive actually modified it, it will just be an
        empty dict.

        This is just a modular way to add storage attributes to the
        Package class, and it's how Spack gets information from the
        packages to the core.

        """
        global __all__

        if isinstance(dicts, string_types):
            dicts = (dicts, )
        if not isinstance(dicts, collections.Sequence):
            message = "dicts arg must be list, tuple, or string. Found {0}"
            raise TypeError(message.format(type(dicts)))
        # Add the dictionary names if not already there
        DirectiveMetaMixin._directive_names |= set(dicts)

        # This decorator just returns the directive functions
        def _decorator(decorated_function):
            __all__.append(decorated_function.__name__)

            @functools.wraps(decorated_function)
            def _wrapper(*args, **kwargs):
                # A directive returns either something that is callable on a
                # package or a sequence of them
                values = decorated_function(*args, **kwargs)

                # ...so if it is not a sequence make it so
                if not isinstance(values, collections.Sequence):
                    values = (values, )

                DirectiveMetaMixin._directives_to_be_executed.extend(values)
            return _wrapper

        return _decorator


directive = DirectiveMetaMixin.directive


@directive('versions')
def version(ver, checksum=None, **kwargs):
    """Adds a version and metadata describing how to fetch it.
    Metadata is just stored as a dict in the package's versions
    dictionary.  Package must turn it into a valid fetch strategy
    later.
    """
    def _execute_version(pkg):
        # TODO: checksum vs md5 distinction is confusing -- fix this.
        # special case checksum for backward compatibility
        if checksum:
            kwargs['md5'] = checksum

        # Store kwargs for the package to later with a fetch_strategy.
        pkg.versions[Version(ver)] = kwargs
    return _execute_version


def _depends_on(pkg, spec, when=None, type=None):
    # If when is False do nothing
    if when is False:
        return
    # If when is None or True make sure the condition is always satisfied
    if when is None or when is True:
        when = pkg.name
    when_spec = parse_anonymous_spec(when, pkg.name)

    dep_spec = Spec(spec)
    if pkg.name == dep_spec.name:
        raise CircularReferenceError(
            "Package '%s' cannot depend on itself." % pkg.name)

    if type is None:
        type = default_deptype
    else:
        type = canonical_deptype(type)

    conditions = pkg.dependencies.setdefault(dep_spec.name, {})
    if when_spec not in conditions:
        conditions[when_spec] = Dependency(dep_spec, type)
    else:
        dependency = conditions[when_spec]
        dependency.spec.constrain(dep_spec, deps=False)
        dependency.type |= set(type)


@directive('conflicts')
def conflicts(conflict_spec, when=None, msg=None):
    """Allows a package to define a conflict.

    Currently, a "conflict" is a concretized configuration that is known
    to be non-valid. For example, a package that is known not to be
    buildable with intel compilers can declare::

        conflicts('%intel')

    To express the same constraint only when the 'foo' variant is
    activated::

        conflicts('%intel', when='+foo')

    Args:
        conflict_spec (Spec): constraint defining the known conflict
        when (Spec): optional constraint that triggers the conflict
        msg (str): optional user defined message
    """
    def _execute_conflicts(pkg):
        # If when is not specified the conflict always holds
        condition = pkg.name if when is None else when
        when_spec = parse_anonymous_spec(condition, pkg.name)

        # Save in a list the conflicts and the associated custom messages
        when_spec_list = pkg.conflicts.setdefault(conflict_spec, [])
        when_spec_list.append((when_spec, msg))
    return _execute_conflicts


@directive(('dependencies'))
def depends_on(spec, when=None, type=None):
    """Creates a dict of deps with specs defining when they apply.
    This directive is to be used inside a Package definition to declare
    that the package requires other packages to be built first.
    @see The section "Dependency specs" in the Spack Packaging Guide."""
    def _execute_depends_on(pkg):
        _depends_on(pkg, spec, when=when, type=type)
    return _execute_depends_on


@directive(('extendees', 'dependencies'))
def extends(spec, **kwargs):
    """Same as depends_on, but dependency is symlinked into parent prefix.

    This is for Python and other language modules where the module
    needs to be installed into the prefix of the Python installation.
    Spack handles this by installing modules into their own prefix,
    but allowing ONE module version to be symlinked into a parent
    Python install at a time.

    keyword arguments can be passed to extends() so that extension
    packages can pass parameters to the extendee's extension
    mechanism.

    """
    def _execute_extends(pkg):
        # if pkg.extendees:
        #     directive = 'extends'
        #     msg = 'Packages can extend at most one other package.'
        #     raise DirectiveError(directive, msg)

        when = kwargs.pop('when', pkg.name)
        _depends_on(pkg, spec, when=when)
        pkg.extendees[spec] = (Spec(spec), kwargs)
    return _execute_extends


@directive('provided')
def provides(*specs, **kwargs):
    """Allows packages to provide a virtual dependency.  If a package provides
       'mpi', other packages can declare that they depend on "mpi", and spack
       can use the providing package to satisfy the dependency.
    """
    def _execute_provides(pkg):
        spec_string = kwargs.get('when', pkg.name)
        provider_spec = parse_anonymous_spec(spec_string, pkg.name)

        for string in specs:
            for provided_spec in spack.spec.parse(string):
                if pkg.name == provided_spec.name:
                    raise CircularReferenceError(
                        "Package '%s' cannot provide itself.")

                if provided_spec not in pkg.provided:
                    pkg.provided[provided_spec] = set()
                pkg.provided[provided_spec].add(provider_spec)
    return _execute_provides


@directive('patches')
def patch(url_or_filename, level=1, when=None, **kwargs):
    """Packages can declare patches to apply to source.  You can
    optionally provide a when spec to indicate that a particular
    patch should only be applied when the package's spec meets
    certain conditions (e.g. a particular version).

    Args:
        url_or_filename (str): url or filename of the patch
        level (int): patch level (as in the patch shell command)
        when (Spec): optional anonymous spec that specifies when to apply
            the patch
        **kwargs: the following list of keywords is supported

            - md5 (str): md5 sum of the patch (used to verify the file
                if it comes from a url)

    """
    def _execute_patch(pkg):
        constraint = pkg.name if when is None else when
        when_spec = parse_anonymous_spec(constraint, pkg.name)

        # if this spec is identical to some other, then append this
        # patch to the existing list.
        cur_patches = pkg.patches.setdefault(when_spec, [])
        cur_patches.append(Patch.create(pkg, url_or_filename, level, **kwargs))

    return _execute_patch


@directive('variants')
def variant(
        name,
        default=None,
        description='',
        values=None,
        multi=False,
        validator=None
):
    """Define a variant for the package. Packager can specify a default
    value as well as a text description.

    Args:
        name (str): name of the variant
        default (str or bool): default value for the variant, if not
            specified otherwise the default will be False for a boolean
            variant and 'nothing' for a multi-valued variant
        description (str): description of the purpose of the variant
        values (tuple or callable): either a tuple of strings containing the
            allowed values, or a callable accepting one value and returning
            True if it is valid
        multi (bool): if False only one value per spec is allowed for
            this variant
        validator (callable): optional group validator to enforce additional
            logic. It receives a tuple of values and should raise an instance
            of SpackError if the group doesn't meet the additional constraints
    """
    if name in reserved_names:
        raise ValueError("The variant name '%s' is reserved by Spack" % name)

    if values is None:
        if default in (True, False) or (type(default) is str and
                                        default.upper() in ('TRUE', 'FALSE')):
            values = (True, False)
        else:
            values = lambda x: True

    if default is None:
        default = False if values == (True, False) else ''

    default = default
    description = str(description).strip()

    def _execute_variant(pkg):
        if not re.match(spack.spec.identifier_re, name):
            directive = 'variant'
            msg = "Invalid variant name in {0}: '{1}'"
            raise DirectiveError(directive, msg.format(pkg.name, name))

        pkg.variants[name] = Variant(
            name, default, description, values, multi, validator
        )
    return _execute_variant


@directive('resources')
def resource(**kwargs):
    """Define an external resource to be fetched and staged when building the
    package. Based on the keywords present in the dictionary the appropriate
    FetchStrategy will be used for the resource. Resources are fetched and
    staged in their own folder inside spack stage area, and then moved into
    the stage area of the package that needs them.

    List of recognized keywords:

    * 'when' : (optional) represents the condition upon which the resource is
      needed
    * 'destination' : (optional) path where to move the resource. This path
      must be relative to the main package stage area.
    * 'placement' : (optional) gives the possibility to fine tune how the
      resource is moved into the main package stage area.
    """
    def _execute_resource(pkg):
        when = kwargs.get('when', pkg.name)
        destination = kwargs.get('destination', "")
        placement = kwargs.get('placement', None)

        # Check if the path is relative
        if os.path.isabs(destination):
            message = 'The destination keyword of a resource directive '
            'can\'t be an absolute path.\n'
            message += "\tdestination : '{dest}\n'".format(dest=destination)
            raise RuntimeError(message)

        # Check if the path falls within the main package stage area
        test_path = 'stage_folder_root'
        normalized_destination = os.path.normpath(
            join_path(test_path, destination)
        )  # Normalized absolute path

        if test_path not in normalized_destination:
            message = "The destination folder of a resource must fall "
            "within the main package stage directory.\n"
            message += "\tdestination : '{dest}'\n".format(dest=destination)
            raise RuntimeError(message)

        when_spec = parse_anonymous_spec(when, pkg.name)
        resources = pkg.resources.setdefault(when_spec, [])
        name = kwargs.get('name')
        fetcher = from_kwargs(**kwargs)
        resources.append(Resource(name, fetcher, destination, placement))
    return _execute_resource


class DirectiveError(spack.error.SpackError):
    """This is raised when something is wrong with a package directive."""


class CircularReferenceError(DirectiveError):
    """This is raised when something depends on itself."""
