"""Core runtime primitives for Big-Chicken Houdini Intelligence Agent."""

from .path_policy import PROJECT_ROOT, PathPolicyError, validate_project_subpath

__all__ = ["PROJECT_ROOT", "PathPolicyError", "validate_project_subpath"]
