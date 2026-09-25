"""Experimental namespace whose API and behavior are not version-stable.

This package contains self-use facilities whose functionality is settled while
their policy or interface shape is still evolving. Implementations may change,
be renamed, removed, or moved without a breaking-change notice.

Downstream plugins, workflows, and skill packages must not depend on this
namespace. Its persisted artifacts are diagnostic output only and are never
part of framework recovery.
"""

__all__: list[str]
