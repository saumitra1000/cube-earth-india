from backend.field_profile_service import FieldProfileService


class Bootstrap:

    def build(self):
        return FieldProfileService()
