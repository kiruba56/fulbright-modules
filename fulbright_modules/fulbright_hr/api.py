import frappe
from frappe import _
from frappe.utils import cint


def _delete_user_permissions_for_employee(employee: str, user_id: str | None):
	"""Remove User Permissions that reference this Employee (not all UPs for the user)."""
	names = set(
		frappe.get_all(
			"User Permission",
			filters={"allow": "Employee", "for_value": employee},
			pluck="name",
		)
	)
	# Also clear Employee-scoped rows owned by this login, if any used a different shape.
	if user_id:
		names.update(
			frappe.get_all(
				"User Permission",
				filters={
					"user": user_id,
					"allow": "Employee",
					"for_value": employee,
				},
				pluck="name",
			)
		)

	for name in names:
		frappe.delete_doc(
			"User Permission",
			name,
			ignore_permissions=True,
			force=True,
		)


def _delete_login_user(user_id: str | None):
	if not user_id or user_id in ("Administrator", "Guest"):
		return
	if not frappe.db.exists("User", user_id):
		return
	if not frappe.has_permission("User", "delete", user_id):
		frappe.throw(
			_("You need permission to delete User {0}").format(user_id),
			frappe.PermissionError,
		)
	frappe.delete_doc("User", user_id, force=True)


@frappe.whitelist(methods=["POST"])
def delete_employee(name: str, unlink_related: int | str = 0):
	"""Delete an Employee. Optionally unlink User Permissions and delete the User."""
	name = (name or "").strip()
	if not name:
		frappe.throw(_("Employee id is required"))

	if not frappe.has_permission("Employee", "delete"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if not frappe.db.exists("Employee", name):
		frappe.throw(_("Employee {0} not found").format(name))

	unlink = cint(unlink_related)
	user_id = frappe.db.get_value("Employee", name, "user_id")

	if unlink:
		if user_id and not frappe.has_permission("User", "delete", user_id):
			frappe.throw(
				_("Unlink & delete requires permission to delete the login user."),
				frappe.PermissionError,
			)
		_delete_user_permissions_for_employee(name, user_id)
		# Clear the link before removing the User so Employee → User does not block.
		if user_id:
			frappe.db.set_value("Employee", name, "user_id", None, update_modified=False)
			_delete_login_user(user_id)
		# Force only after cleanup — still requires Employee delete permission above.
		frappe.delete_doc("Employee", name, force=True)
	else:
		frappe.delete_doc("Employee", name)

	return {"ok": True, "name": name, "unlinked": bool(unlink)}
