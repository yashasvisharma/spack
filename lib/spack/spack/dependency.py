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
"""Data structures that represent Spack's dependency relationships.
"""
from six import string_types

import spack


#: The types of dependency relationships that Spack understands.
all_deptypes = ('build', 'link', 'run')

#: Default dependency type if none is specified
default_deptype = ('build', 'link')


def canonical_deptype(deptype):
    """Convert deptype to a canonical sorted tuple, or raise ValueError.

    Args:
        deptype (str or list or tuple): string representing dependency
            type, or a list/tuple of such strings.  Can also be the
            builtin function ``all`` or the string 'all', which result in
            a tuple of all dependency types known to Spack.
    """
    if deptype in (None, 'all', all):
        return all_deptypes

    elif isinstance(deptype, string_types):
        if deptype not in all_deptypes:
            raise ValueError('Invalid dependency type: %s' % deptype)
        return (deptype,)

    elif isinstance(deptype, (tuple, list)):
        invalid = next((d for d in deptype if d not in all_deptypes), None)
        if invalid:
            raise ValueError('Invalid dependency type: %s' % invalid)
        return tuple(sorted(deptype))

    return deptype


class Dependency(object):
    """Class representing metadata for a dependency on a package.

    This class differs from ``spack.spec.DependencySpec`` because it
    represents metadata at the ``Package`` level.
    ``spack.spec.DependencySpec`` is a descriptor for an actual package
    confiuguration, while ``Dependency`` is a descriptor for a package's
    dependency *requirements*.

    A dependency is a requirement for a configuration of another package
    that satisfies a particular spec.  The dependency can have *types*,
    which determine *how* that packge configuration is required,
    e.g. whether it is required for building the package, whether it
    needs to be linked to, or whether it is needed at runtime so that
    Spack can call commands from it.

    A package can also depend on another package with *patches*. This is
    for cases where the maintainers of one package also maintain special
    patches for their dependencies.  If one package depends on another
    with patches, a special version of that dependency with patches
    applied will be built for use by the dependent package.  The patches
    are included in the new version's spec hash to differentiate it from
    unpatched versions of the same package, so that unpatched versions of
    the dependency package can coexist with the patched version.

    """
    def __init__(self, pkg, spec, type=None):
        """Create a new Dependency.

        Args:
            pkg (type): Package that has this dependency
            spec (Spec): Spec indicating dependency requirements
            patches (list): list of patches to apply to this dependency
            type (sequence): strings describing dependency relationship
        """
        assert isinstance(spec, spack.spec.Spec)

        self.pkg = pkg
        self.spec = spec.copy()

        # This dict maps condition specs to lists of Patch objects, just
        # as the patches dict on packages does.
        self.patches = {}

        if type is None:
            self.type = set(default_deptype)
        else:
            self.type = set(type)

    @property
    def name(self):
        """Get the name of the package to be patched.

        This is needed so that a ``Dependency`` acts like a ``Package``
        object when passed to a directive.
        """
        return self.spec.name

    def merge(self, other):
        """Merge constraints, deptypes, and patches of other into self."""
        self.spec.constrain(other.spec)
        self.type |= other.type

        # concatenate patch lists, or just copy them in
        for cond, p in other.patches.items():
            if cond in self.patches:
                self.patches[cond].extend(other.patches[cond])
            else:
                self.patches[cond] = other.patches[cond]
