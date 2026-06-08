from core.profile_builder import ProfileBuilder


class FieldProfileService:

    def __init__(self):
        self.builder = ProfileBuilder()

    async def build_profile(self, lat, lng, year, parcel_override=None):
        if not (-90 <= lat <= 90):
            raise ValueError(f"Invalid latitude {lat}")
        if not (-180 <= lng <= 180):
            raise ValueError(f"Invalid longitude {lng}")
        return await self.builder.build(lat, lng, year, parcel_override=parcel_override)


service = FieldProfileService()
