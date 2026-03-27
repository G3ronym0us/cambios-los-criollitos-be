from pydantic import BaseModel, Field, validator
from datetime import datetime
from typing import Optional, List
from uuid import UUID


# ===== Split Schemas =====

class ConfigSplitBase(BaseModel):
    """Schema base para división de comisión en configuración"""
    user_uuid: UUID
    percentage: float = Field(..., ge=0, le=100, description="Porcentaje de comisión (0-100)")


class ConfigSplitCreate(ConfigSplitBase):
    """Schema para crear división de comisión"""
    pass


class ConfigSplitResponse(ConfigSplitBase):
    """Schema de respuesta para división de comisión"""
    uuid: UUID
    configuration_uuid: UUID
    username: Optional[str] = None
    user_full_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ===== Configuration Schemas =====

class CommissionConfigBase(BaseModel):
    """Schema base para configuración de comisiones"""
    currency_pair_uuid: UUID = Field(..., description="UUID del par de divisas")
    name: str = Field(..., min_length=1, max_length=100, description="Nombre de la configuración")
    description: Optional[str] = Field(None, description="Descripción opcional")
    total_percentage: float = Field(..., ge=0, le=100, description="Porcentaje total de ganancia")
    fund_group_uuid: Optional[UUID] = Field(None, description="UUID del fondo asociado (opcional). Al crear transacciones con esta config se generará un FundMovement EXCHANGE automáticamente")


class CommissionConfigCreate(CommissionConfigBase):
    """Schema para crear configuración de comisiones"""
    splits: List[ConfigSplitCreate] = Field(..., min_items=1, description="Divisiones de comisión")

    @validator('splits')
    def validate_splits_sum(cls, v, values):
        """Validar que la suma de porcentajes coincida con el total"""
        if v and 'total_percentage' in values:
            total_split = sum(split.percentage for split in v)
            total_config = values['total_percentage']

            # Permitir diferencias pequeñas por redondeo (0.01%)
            if abs(total_split - total_config) > 0.01:
                raise ValueError(
                    f'Sum of split percentages ({total_split}%) must equal total_percentage ({total_config}%)'
                )
        return v


class CommissionConfigUpdate(BaseModel):
    """Schema para actualizar configuración de comisiones"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    total_percentage: Optional[float] = Field(None, ge=0, le=100)
    is_active: Optional[bool] = None
    splits: Optional[List[ConfigSplitCreate]] = None
    fund_group_uuid: Optional[UUID] = Field(None, description="UUID del fondo asociado. Pasar null para desvincular")

    @validator('splits')
    def validate_splits_sum(cls, v, values):
        """Validar que la suma de porcentajes coincida con el total si se actualizan ambos"""
        if v and 'total_percentage' in values and values['total_percentage'] is not None:
            total_split = sum(split.percentage for split in v)
            total_config = values['total_percentage']

            if abs(total_split - total_config) > 0.01:
                raise ValueError(
                    f'Sum of split percentages ({total_split}%) must equal total_percentage ({total_config}%)'
                )
        return v


class CommissionConfigResponse(CommissionConfigBase):
    """Schema de respuesta para configuración de comisiones"""
    uuid: UUID
    pair_symbol: Optional[str] = None  # Incluido para compatibilidad
    fund_group_name: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    created_by_user_uuid: Optional[UUID] = None
    splits: List[ConfigSplitResponse] = []

    class Config:
        from_attributes = True


class CommissionConfigList(BaseModel):
    """Lista paginada de configuraciones"""
    configurations: List[CommissionConfigResponse]
    total: int
    page: int
    per_page: int
    total_pages: int


class CommissionConfigSummary(BaseModel):
    """Resumen de configuración (sin splits detallados)"""
    uuid: UUID
    currency_pair_uuid: UUID
    pair_symbol: Optional[str] = None  # Incluido para compatibilidad
    name: str
    description: Optional[str] = None
    total_percentage: float
    split_count: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class PairConfigsResponse(BaseModel):
    """Respuesta con configuraciones disponibles para un par"""
    currency_pair_uuid: UUID
    pair_symbol: Optional[str] = None
    configurations: List[CommissionConfigResponse]
    total: int
