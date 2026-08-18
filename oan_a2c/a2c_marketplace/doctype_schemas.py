from pydantic import BaseModel, Field, field_validator, model_validator


class ProductMetaSchema(BaseModel):
	meta_key: str = Field(..., min_length=1, max_length=140)
	meta_value: str = Field(..., min_length=1, max_length=2000)


class SingleProductSchema(BaseModel):
	product_name: str = Field(..., min_length=1, max_length=140)
	min_interest_rate: float = Field(..., ge=0, le=20.0)
	max_interest_rate: float | None = Field(None, ge=0, le=20.0)
	min_amount: int | None = Field(None, ge=0, le=999999)
	max_amount: int = Field(..., ge=0, le=999999)
	tenure_months: int = Field(..., ge=1, le=1200)
	description: str | None = Field(None, max_length=2000)
	image: str | None = Field(None, max_length=500)
	product_meta: list[ProductMetaSchema] | None = None

	@field_validator("min_interest_rate", "max_interest_rate")
	@classmethod
	def validate_decimals(cls, v):
		if v is not None and round(v, 2) != v:
			raise ValueError("Interest rate must have at most 2 decimal places.")
		return v

	@model_validator(mode="after")
	def validate_min_max_ordering(self):
		if self.min_interest_rate is not None and self.max_interest_rate is not None:
			if self.min_interest_rate > self.max_interest_rate:
				raise ValueError("min_interest_rate cannot be greater than max_interest_rate.")
		if self.min_amount is not None and self.max_amount is not None:
			if self.min_amount > self.max_amount:
				raise ValueError("min_amount cannot be greater than max_amount.")
		return self
