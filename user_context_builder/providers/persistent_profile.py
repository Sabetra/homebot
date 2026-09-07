"""
Persistent Profile Provider for user context building.

Fetches and formats persistent user profile data.
"""

from typing import Optional, Dict, Any
import logging
from user_context_builder.base import BaseContextProvider
from user_context_builder.models import UserContextRequest

logger = logging.getLogger(__name__)


class PersistentProfileProvider(BaseContextProvider):
    """Provider for fetching persistent user profile data."""
    
    def __init__(
        self,
        profile_cache: Optional[Any] = None,
        priority: int = 50,
    ):
        """
        Initialize the Persistent Profile Provider.
        
        Args:
            profile_cache: Optional ProfileCacheManager for synthesized persistent profiles
            priority: Provider priority (lower = higher priority)
        """
        super().__init__(name="persistent_profile", priority=priority)
        self.profile_cache = profile_cache
    
    def provide(
        self,
        request: UserContextRequest,
        session_manager: Any
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch persistent profile for the user.
        
        Args:
            request: User context request
            session_manager: Session manager with DB access
            
        Returns:
            Profile dictionary or None if not found
        """
        try:
            profile = self._get_from_profile_cache(request.user_id)
            if profile:
                return profile

            db_manager = self._resolve_db_manager(session_manager)
            if db_manager is None:
                logger.warning("DB manager not available")
                return None

            # Try to get persistent profile
            profile = self._get_persistent_profile(db_manager, request.user_id)
            
            if not profile:
                logger.debug(f"No persistent profile found for user {request.user_id}")
                return None
            
            return profile
        
        except Exception as e:
            logger.error(f"Error fetching persistent profile: {e}", exc_info=True)
            return None

    def _resolve_db_manager(self, session_manager: Any) -> Optional[Any]:
        db_manager = getattr(session_manager, 'db_manager', None)
        if db_manager is not None:
            return db_manager

        manager = getattr(session_manager, 'manager', None)
        if manager is not None:
            return getattr(manager, 'db', None)

        return None

    def _get_from_profile_cache(self, user_id: str) -> Optional[Dict[str, Any]]:
        if self.profile_cache is None:
            return None

        profile = self.profile_cache.get_cached_profile(user_id)
        if profile is None:
            return None

        if hasattr(profile, 'to_context_dict'):
            return profile.to_context_dict()

        if isinstance(profile, dict):
            return profile

        return {
            'core_personality': getattr(profile, 'core_personality', {}),
            'current_state': getattr(profile, 'current_state', {}),
            'relationships': getattr(profile, 'relationships', {}),
            'goals_and_growth': getattr(profile, 'goals_and_growth', {}),
            'coping_and_resources': getattr(profile, 'coping_and_resources', {}),
            'therapeutic_focus': getattr(profile, 'therapeutic_focus', {}),
            'overall_confidence': getattr(profile, 'overall_confidence', 0.0),
            'version': getattr(profile, 'version', 0),
            'updated_at': getattr(profile, 'updated_at', ''),
        }
    
    def _get_persistent_profile(
        self,
        db_manager: Any,
        user_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get persistent profile from database.
        
        Args:
            db_manager: Database manager instance
            user_id: User identifier
            
        Returns:
            Profile dictionary or None
        """
        # Try various methods to get profile
        if hasattr(db_manager, 'get_user_profile'):
            profile_raw = db_manager.get_user_profile(user_id=user_id)
            if profile_raw:
                # Type guard: ensure dict
                if not isinstance(profile_raw, dict):
                    logger.warning(f"get_user_profile returned non-dict: {type(profile_raw)}")
                    return None
                return profile_raw
        
        if hasattr(db_manager, 'get_persistent_profile'):
            profile_raw = db_manager.get_persistent_profile(user_id=user_id)
            if profile_raw:
                # Type guard: ensure dict
                if not isinstance(profile_raw, dict):
                    logger.warning(f"get_persistent_profile returned non-dict: {type(profile_raw)}")
                    return None
                return profile_raw
        
        # Try to query user metadata
        if hasattr(db_manager, 'get_user_metadata'):
            metadata_raw = db_manager.get_user_metadata(user_id=user_id)
            if metadata_raw:
                # Type guard: ensure dict
                if not isinstance(metadata_raw, dict):
                    logger.warning(f"get_user_metadata returned non-dict: {type(metadata_raw)}")
                    return None
                return metadata_raw
        
        logger.debug("DB manager has no method to retrieve persistent profile")
        return None
