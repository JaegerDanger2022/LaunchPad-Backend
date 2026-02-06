"""
Database models for Victory Wall community features
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Literal
from pydantic import BaseModel, Field, field_validator


# ====================
# REQUEST MODELS
# ====================

class CreateVictoryRequest(BaseModel):
    """Request model for creating a victory card"""
    milestoneId: str = Field(..., description="ID of the completed milestone")
    evidenceSnippet: Optional[str] = Field(None, max_length=200, description="User's proof text (optional), max 200 characters")
    isAnonymous: bool = Field(..., description="Whether to hide user identity")
    impact: Optional[Literal["critical", "high", "medium", "low"]] = Field(None, description="Impact level")


class UpdateCommunityProfileRequest(BaseModel):
    """Request model for updating user's community profile"""
    location: Optional[str] = Field(None, description="User's location")
    age: Optional[int] = Field(None, ge=1, le=120, description="User's age (positive integer)")
    shareAnonymousByDefault: Optional[bool] = Field(None, description="Default sharing preference")


# ====================
# RESPONSE MODELS
# ====================

class VictoryCardResponse(BaseModel):
    """Response model for a single victory card"""
    id: str
    userId: str
    userDisplayName: str
    userLocation: Optional[str] = None
    userAge: Optional[int] = None

    milestoneId: str
    milestoneTitle: str
    dreamId: str
    dreamCategory: str

    evidenceSnippet: Optional[str] = None  # Optional - can be blank
    confidenceBoost: int  # From milestone XP
    impactLevel: str

    completedDate: str  # ISO date
    createdAt: str  # ISO date

    courageBoosts: int
    hasUserBoosted: Optional[bool] = None  # Whether requesting user has boosted this victory
    permissionsCount: int = 0  # Count of permission slips received
    meTooCount: int = 0  # Count of Me Too clicks
    hasUserMeTooed: Optional[bool] = None  # Whether requesting user has Me Too'd this victory
    isAnonymous: bool


class PaginationInfo(BaseModel):
    """Pagination metadata"""
    page: int
    limit: int
    totalPages: int
    totalCount: int


class VictoriesListResponse(BaseModel):
    """Response model for victory cards list"""
    victories: list[VictoryCardResponse]
    pagination: PaginationInfo


class CreateVictoryResponse(BaseModel):
    """Response for creating a victory"""
    success: bool
    victoryId: str
    couragePointsAwarded: int


class BoostVictoryResponse(BaseModel):
    """Response for boosting a victory"""
    success: bool
    newBoostCount: int
    couragePointsAwarded: int


class CommunityStatsResponse(BaseModel):
    """Response for user's community stats"""
    victoriesShared: int
    boostsReceived: int
    boostsGiven: int
    permissionsReceived: int
    permissionsGiven: int
    couragePoints: int


class CommunityProfile(BaseModel):
    """User's community profile"""
    location: Optional[str] = None
    age: Optional[int] = None
    shareAnonymousByDefault: bool = False


class UpdateCommunityProfileResponse(BaseModel):
    """Response for updating community profile"""
    success: bool
    message: str
    communityProfile: CommunityProfile


# ====================
# DATABASE MODELS
# ====================

class VictoryCardDB(BaseModel):
    """Victory Card database document"""
    id: str
    userId: str
    userDisplayName: str
    userLocation: Optional[str] = None
    userAge: Optional[int] = None

    milestoneId: str
    milestoneTitle: str
    dreamId: str
    dreamCategory: str

    evidenceSnippet: Optional[str] = None  # Optional - can be blank
    confidenceBoost: int
    impactLevel: str

    completedDate: str  # ISO date
    createdAt: str  # ISO date

    courageBoosts: int = 0
    permissionsCount: int = 0  # NEW: Count of permission slips received
    meTooCount: int = 0  # NEW: Count of Me Too clicks
    hasUserBoosted: Dict[str, bool] = Field(default_factory=dict)  # Map for quick lookup

    isAnonymous: bool

    def to_response(self) -> VictoryCardResponse:
        """Convert database model to response model"""
        return VictoryCardResponse(
            id=self.id,
            userId=self.userId,
            userDisplayName=self.userDisplayName,
            userLocation=self.userLocation,
            userAge=self.userAge,
            milestoneId=self.milestoneId,
            milestoneTitle=self.milestoneTitle,
            dreamId=self.dreamId,
            dreamTitle=self.dreamTitle,
            dreamCategory=self.dreamCategory,
            evidenceSnippet=self.evidenceSnippet,
            confidenceBoost=self.confidenceBoost,
            impactLevel=self.impactLevel,
            completedDate=self.completedDate,
            createdAt=self.createdAt,
            courageBoosts=self.courageBoosts,
            permissionsCount=self.permissionsCount,
            meTooCount=self.meTooCount,
            isAnonymous=self.isAnonymous
        )


class CourageBoostDB(BaseModel):
    """Courage Boost database document"""
    id: str
    victoryCardId: str
    giverId: str
    receiverId: str
    createdAt: str  # ISO date


class PermissionSlipDB(BaseModel):
    """Permission Slip database document"""
    id: str
    victoryCardId: str
    giverId: str
    giverDisplayName: str  # "Sarah" or "Anonymous"
    receiverId: str
    permissionType: int  # 1, 2, 3, or 4
    permissionText: str
    createdAt: str  # ISO date


# ====================
# PERMISSION SLIP MODELS
# ====================

class GivePermissionRequest(BaseModel):
    """Request model for giving a permission slip"""
    permissionType: int = Field(..., ge=1, le=4, description="Permission type: 1, 2, 3, or 4")


class GivePermissionResponse(BaseModel):
    """Response for giving a permission slip"""
    success: bool
    permissionText: str
    couragePointsAwarded: int


class PermissionSlipResponse(BaseModel):
    """Response model for a single permission slip"""
    id: str
    giverDisplayName: str
    permissionText: str
    createdAt: str


class VictoryPermissionsResponse(BaseModel):
    """Response for listing permissions on a victory"""
    permissions: list[PermissionSlipResponse]
    count: int
    total: int


# ====================
# ME TOO MODELS
# ====================

class MeTooDB(BaseModel):
    """Me Too database document"""
    id: str
    victoryCardId: str
    userId: str
    createdAt: str  # ISO date


class ToggleMeTooResponse(BaseModel):
    """Response for toggling Me Too"""
    success: bool
    newMeTooCount: int
    added: bool  # true if added, false if removed


class InspirationItem(BaseModel):
    """Single inspiration item in user's list"""
    id: str  # Victory card ID
    milestoneTitle: str
    dreamCategory: str
    userDisplayName: str
    createdAt: str  # Victory creation date
    meTooDate: str  # When user clicked Me Too


class InspirationsResponse(BaseModel):
    """Response for user's inspirations list"""
    inspirations: list[InspirationItem]
    total: int


# ====================
# UTILITY FUNCTIONS
# ====================

def generate_victory_id() -> str:
    """Generate a unique victory card ID"""
    import uuid
    return f"vic_{uuid.uuid4().hex[:12]}"


def generate_boost_id() -> str:
    """Generate a unique courage boost ID"""
    import uuid
    return f"boost_{uuid.uuid4().hex[:12]}"


def get_current_iso_timestamp() -> str:
    """Get current timestamp in ISO format"""
    return datetime.now(timezone.utc).isoformat()


# Dream categories validation list
DREAM_CATEGORIES = [
    "career_professional",
    "personal_development",
    "health_wellness",
    "creative_expression",
    "relationships_community",
    "travel_exploration",
    "finance_security",
    "lifestyle_hobbies",
    "courage_challenges",
    "achievement_goals"
]

# Impact levels validation list
IMPACT_LEVELS = ["critical", "high", "medium", "low"]


def generate_permission_id() -> str:
    """Generate a unique permission slip ID"""
    import uuid
    return f"perm_{uuid.uuid4().hex[:12]}"


def generate_metoo_id() -> str:
    """Generate a unique Me Too ID"""
    import uuid
    return f"metoo_{uuid.uuid4().hex[:12]}"


def get_permission_text(permission_type: int, dream_category: str) -> str:
    """
    Generate permission text based on type and dream category

    Args:
        permission_type: 1, 2, 3, or 4
        dream_category: The dream category from DREAM_CATEGORIES

    Returns:
        The permission text string
    """
    if permission_type == 1:
        return "Permission granted to keep going"
    elif permission_type == 2:
        return "Permission granted to be proud of this"
    elif permission_type == 3:
        return "Permission granted to inspire the rest of us"
    elif permission_type == 4:
        # Category-specific permissions
        category_permissions = {
            'career_professional': 'Permission granted to call yourself a leader',
            'personal_development': 'Permission granted to call yourself a learner',
            'health_wellness': 'Permission granted to call yourself an athlete',
            'creative_expression': 'Permission granted to call yourself a creator',
            'relationships_community': 'Permission granted to call yourself a connector',
            'travel_exploration': 'Permission granted to call yourself a traveler',
            'finance_security': 'Permission granted to call yourself financially savvy',
            'lifestyle_hobbies': 'Permission granted to call yourself dedicated',
            'courage_challenges': 'Permission granted to call yourself brave',
            'achievement_goals': 'Permission granted to call yourself a champion'
        }
        return category_permissions.get(dream_category, "Permission granted to call yourself amazing")
    else:
        return "Permission granted"
