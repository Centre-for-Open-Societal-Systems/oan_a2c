from pydantic import BaseModel, Field, field_validator, model_validator

# Global limits for A2C Loan Products.
#
# Single source of truth: every schema that bounds a loan amount, tenure or
# interest rate imports these, and the catalog facet endpoint publishes the same
# numbers as slider boundaries. Changing a cap here changes it everywhere, so the
# UI can never offer a range the API will reject.
MAX_LOAN_AMOUNT = 999999.0
MAX_TENURE_MONTHS = 1200
MAX_INTEREST_RATE = 20.0

# The widest value a *filter* may express, which is deliberately not MAX_LOAN_AMOUNT.
# That cap governs what a bank may offer in the catalogue; loan applications and credit
# information are written through other paths (see api/v1/leads.py) that permit larger
# figures. Binding a search bound to the catalogue cap would make any loan above it
# impossible to search for even though it exists in the data.
MAX_QUERY_AMOUNT = 999999999999.0

# Bank onboarding & profile validation rules
POSTAL_CODE_MIN_LENGTH = 4
POSTAL_CODE_MAX_LENGTH = 6
POSTAL_CODE_REGEX = r"^\d{4,6}$"
WEBSITE_REGEX = r"^(https?:\/\/)?(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{2,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)$"


class ProductMetaSchema(BaseModel):
	meta_key: str = Field(..., min_length=1, max_length=140)
	meta_value: str = Field(..., min_length=1, max_length=2000)


class SingleProductSchema(BaseModel):
	product_name: str = Field(..., min_length=1, max_length=140)
	min_interest_rate: float = Field(..., ge=0, le=MAX_INTEREST_RATE)
	max_interest_rate: float | None = Field(None, ge=0, le=MAX_INTEREST_RATE)
	min_amount: int | None = Field(None, ge=0, le=MAX_LOAN_AMOUNT)
	max_amount: int = Field(..., ge=0, le=MAX_LOAN_AMOUNT)
	tenure_months: int = Field(..., ge=1, le=MAX_TENURE_MONTHS)
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
