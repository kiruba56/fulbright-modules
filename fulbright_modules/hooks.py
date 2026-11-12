app_name = "fulbright_modules"
app_title = "Fulbright Modules"
app_publisher = "Fulbright"
app_description = "Shared Fulbright Frappe modules for the Fulbright app"
app_email = "mail@example.com"
app_license = "mit"

before_request = ["fulbright_modules.auth.async_email.patch_otp_email"]

doc_events = {
	"User": {
		"before_insert": "fulbright_modules.auth.welcome.suppress_stock_welcome_mail",
	},
}

after_install = "fulbright_modules.install.after_install"
after_migrate = "fulbright_modules.install.after_migrate"
