class ValidationError(Exception):
    def __init__(self, error_type, message, details=None):
        self.error_type = error_type
        self.message = message
        self.details = details
        super().__init__(self.message)

def validate_task_spec(spec: dict) -> dict:
    """
    Validates Task Spec JSON. Supports only quote_verify.
    """
    if not isinstance(spec, dict):
        raise ValidationError("invalid_task_spec", "Task spec is invalid.")
        
    task_type = spec.get("task_type")
    if task_type != "quote_verify":
        raise ValidationError("invalid_task_spec", "Task spec is invalid.")
        
    primary_key = spec.get("primary_key")
    if not primary_key:
        primary_key = "id"
        
    import copy
    validated_spec = copy.deepcopy(spec)
    validated_spec["primary_key"] = str(primary_key).strip()
    return validated_spec
