app_name = "fulbright_modules"
app_title = "Fulbright Modules"
app_publisher = "Fulbright"
app_description = "Shared Fulbright Frappe modules for the Fulbright app"
app_email = "mail@example.com"
app_license = "mit"

before_request = ["fulbright_modules.auth.async_email.patch_otp_email"]

after_install = "fulbright_modules.gmail.setup.ensure_custom_fields"
after_migrate = "fulbright_modules.gmail.setup.ensure_custom_fields"
