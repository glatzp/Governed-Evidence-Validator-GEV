# SYSTEM BOUNDARY — DO NOT EXECUTE DIRECTLY
# Deterministic validation engine — Boundary Stub
# Must be called only through governed/launch_qa.py or web/main.py
# Direct use bypasses governance and is unsupported

from validator.schema_validator import validate_task_spec, ValidationError
from governed.audit_logger import write_audit_event

def run_validation_pipeline(
    source_path: str,
    output_path: str,
    spec: dict,
    preview_confirmed: bool = False
) -> dict:
    """
    Main validation entrypoint. Legacy CSV pipeline is retired.
    quote_verify tasks are routed to the manual Q&A interface.
    """
    try:
        # Validate task spec
        validated_spec = validate_task_spec(spec)
        task_type = validated_spec.get("task_type")

        if task_type == "quote_verify":
            # Event type constant string inline to avoid importing audit_logger constants if deleted
            write_audit_event("QA_ROUTED_TO_MANUAL_INTERFACE", details={"task_type": "quote_verify"})
            return {
                "status": "ROUTED_TO_MANUAL_INTERFACE",
                "message": (
                    "Document Q&A tasks (quote_verify) use the manual hand-off interface "
                    "and cannot be executed through the CSV validation pipeline. "
                    "Run: python launch_qa.py --document <path> --question \"<question>\""
                ),
                "action": "Use launch_qa.py for document Q&A tasks.",
            }
        
        raise ValidationError("invalid_task_spec", "Unsupported task type.")

    except ValidationError as e:
        return {
            "status": "ERROR",
            "message": "Validation stopped before comparison.",
            "recommendation": "Validation not completed.",
            "error_type": e.error_type,
            "error_message": e.message
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": "Validation stopped before comparison.",
            "recommendation": "Validation not completed.",
            "error_type": "internal_error",
            "error_message": str(e)
        }

if __name__ == "__main__":
    print("This validator engine is sealed and must not be executed directly.\nUse: python -m governed.governed_app")
    import sys
    sys.exit(1)
