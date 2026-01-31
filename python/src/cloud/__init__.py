"""
Cloud Module - Cloud storage and sync functionality
"""

from .storage_provider import CloudStorageProvider
from .storage_factory import get_storage_provider

__all__ = ['CloudStorageProvider', 'get_storage_provider']
