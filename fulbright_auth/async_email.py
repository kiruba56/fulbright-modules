import threading
import traceback

import frappe


def start_immediately(callback):
	"""Run callback on a new site connection and commit, without waiting for it."""
	site = frappe.local.site
	sites_path = frappe.local.sites_path

	def run():
		try:
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			callback()
			frappe.db.commit()
		except Exception:
			message = traceback.format_exc()
			try:
				frappe.db.rollback()
			except Exception:
				pass
			try:
				frappe.log_error(title="Fulbright email failed", message=message)
				frappe.db.commit()
			except Exception:
				pass
		finally:
			try:
				frappe.destroy()
			except Exception:
				pass

	threading.Thread(target=run, daemon=True).start()


def patch_otp_email():
	"""Return from login as soon as the code exists. Send the email immediately."""
	import frappe.twofactor as twofactor

	if getattr(twofactor, "_fulbright_async_otp", False):
		return

	def send_token_via_email(user, token, otp_secret, otp_issuer, subject=None, message=None):
		import pyotp

		user_email = frappe.db.get_value("User", user, "email")
		if not user_email:
			return False

		otp = pyotp.HOTP(otp_secret).at(int(token))
		template_args = {"otp": otp, "otp_issuer": otp_issuer}
		mail_subject = subject or twofactor.get_email_subject_for_2fa(template_args)
		mail_message = message or twofactor.get_email_body_for_2fa(template_args)

		def send():
			frappe.sendmail(
				recipients=user_email,
				subject=mail_subject,
				message=mail_message,
				now=True,
				with_container=True,
				wrapper="templates/emails/auth_email.html",
				retry=3,
			)

		start_immediately(send)
		return True

	twofactor.send_token_via_email = send_token_via_email
	twofactor._fulbright_async_otp = True
