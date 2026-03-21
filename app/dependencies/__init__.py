from app.dependencies.auth import (
    get_current_user,
    get_current_admin,
    get_current_student,
    get_current_teacher,
    get_onboarded_student,
    get_onboarded_teacher,
)

# Aliases for convenience
require_admin = get_current_admin
require_student = get_current_student
require_teacher = get_current_teacher
