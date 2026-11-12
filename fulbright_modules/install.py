def after_install():
	from fulbright_modules.fulbright_hr.setup import ensure_custom_fields as ensure_hr_fields
	from fulbright_modules.gmail.setup import ensure_custom_fields as ensure_gmail_fields

	ensure_gmail_fields()
	ensure_hr_fields()


def after_migrate():
	after_install()
