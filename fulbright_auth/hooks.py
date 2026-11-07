app_name = "fulbright_auth"
app_title = "Fulbright Auth"
app_publisher = "Fulbright"
app_description = "Email sign-in links that open the Fulbright app"
app_email = "mail@example.com"
app_license = "mit"

before_request = ["fulbright_auth.async_email.patch_otp_email"]
