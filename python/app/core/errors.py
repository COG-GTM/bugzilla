"""Bugzilla WebService error contract.

Perl: Bugzilla/WebService/Constants.pm (WS_ERROR_CODE, REST_STATUS_CODE_MAP) and
Bugzilla/WebService/Server/REST.pm (error envelope). Messages are the rendered
template/global/user-error.html.tmpl / code-error.html.tmpl strings; keep them byte-exact.
"""

from __future__ import annotations

from typing import Any

DOCUMENTATION_URL = "https://bugzilla.readthedocs.org/en/5.0/api/"

# name -> numeric code (subset of WS_ERROR_CODE that the Bug/User/Bugzilla slices raise)
WS_ERROR_CODE: dict[str, int] = {
    # generic
    "object_not_specified": 50, "reassign_to_empty": 50, "param_required": 50, "params_required": 50,
    "undefined_field": 50, "object_does_not_exist": 51, "param_must_be_numeric": 52, "number_not_numeric": 52,
    "param_invalid": 53, "number_too_large": 54, "number_too_small": 55, "illegal_date": 56,
    "param_integer_required": 57, "param_scalar_array_required": 58,
    # bug
    "improper_bug_id_field_value": 100, "bug_id_does_not_exist": 101, "bug_access_denied": 102,
    "bug_access_query": 102, "alias_too_long": 103, "alias_in_use": 103, "alias_is_numeric": 103,
    "alias_has_comma_or_space": 103, "multiple_alias_not_allowed": 103, "illegal_field": 104,
    "freetext_too_long": 104, "require_component": 105, "component_name_too_long": 105,
    "product_unknown_component": 105, "no_products": 106, "entry_access_denied": 106,
    "product_access_denied": 106, "product_disabled": 106, "require_summary": 107, "invalid_field_name": 108,
    "product_edit_denied": 109, "comment_is_private": 110, "comment_id_invalid": 111, "comment_too_long": 114,
    "comment_invalid_isprivate": 117, "comment_tag_disabled": 125, "comment_tag_invalid": 126,
    "comment_tag_too_long": 127, "comment_tag_too_short": 128, "bug_url_invalid": 112, "bug_url_too_long": 112,
    "user_not_insider": 113, "illegal_change": 115, "dependency_loop_single": 116, "dependency_loop_multi": 116,
    "dupe_loop_detected": 118, "dupe_id_required": 119, "group_invalid_removal": 120,
    "group_restriction_not_allowed": 120, "missing_resolution": 121, "resolution_not_allowed": 122,
    "illegal_bug_status_transition": 123, "flag_status_invalid": 129, "flag_update_denied": 130,
    "flag_type_requestee_disabled": 131, "flag_not_unique": 132, "flag_type_not_unique": 133,
    "flag_type_inactive": 134,
    # auth
    "invalid_login_or_password": 300, "invalid_username_or_password": 300, "account_disabled": 301,
    "auth_invalid_email": 302, "extern_id_conflict": -303, "auth_failure": 304, "password_too_short": 305,
    "password_not_complex": 305, "api_key_not_valid": 306, "api_key_revoked": 306, "auth_invalid_token": 307,
    "login_required": 410,
    # users
    "account_exists": 500, "illegal_email_address": 501, "auth_cant_create_account": 501,
    "account_creation_disabled": 501, "account_creation_restricted": 501, "invalid_username": 504,
    "invalid_user_group": 504, "user_access_by_id_denied": 505, "user_access_by_match_denied": 505,
    # attachments
    "file_too_large": 600, "invalid_content_type": 601, "file_not_specified": 603,
    "missing_attachment_description": 604, "zero_length_file": 606,
    # products / groups / classifications / search / flagtypes / components
    "product_blank_name": 700, "product_name_too_long": 701, "product_name_already_in_use": 702,
    "product_name_diff_in_case": 702, "product_must_have_description": 703, "product_must_have_version": 704,
    "product_must_define_defaultmilestone": 705, "empty_group_name": 800, "group_exists": 801,
    "empty_group_description": 802, "invalid_regexp": 803, "invalid_group_name": 804, "group_cannot_view": 805,
    "auth_classification_not_enabled": 900, "buglist_parameters_required": 1000,
    "flag_type_name_invalid": 1101, "flag_type_description_invalid": 1102, "flag_type_cc_list_invalid": 1103,
    "flag_type_sortkey_invalid": 1104, "flag_type_not_editable": 1105, "component_already_exists": 1200,
    "component_is_last": 1201, "component_has_bugs": 1202,
    # transport
    "xmlrpc_invalid_value": -32600, "unknown_method": -32601, "json_rpc_post_only": 32610,
    "json_rpc_invalid_callback": 32611, "xmlrpc_illegal_content_type": 32612,
    "json_rpc_illegal_content_type": 32613, "rest_invalid_resource": 32614,
    # anything else (e.g. invalid_cookies_or_token) is ERROR_UNKNOWN_TRANSIENT
    "invalid_cookies_or_token": 32000, "internal_error": 100500,
}

# Perl REST maps selected error codes to HTTP statuses; everything else is 400.
REST_STATUS_CODE_MAP: dict[int, int] = {
    51: 404, 101: 404, 32614: 404,
    102: 401, 106: 401, 109: 401, 110: 401, 113: 401, 115: 401, 120: 401,
    300: 401, 301: 401, 302: 401, 303: 401, 304: 401, 410: 401, 504: 401, 505: 401,
    32000: 400, 32001: 500, 32002: 500, 32610: 415, 32612: 406, 32613: 500, 32614: 404, 100500: 500,
}


# message keys that are variants of one Perl error name
ERROR_NAME_ALIASES = {
    "invalid_bug_id_non_existent": "bug_id_does_not_exist",
    "invalid_bug_id_or_alias": "improper_bug_id_field_value",
    "illegal_bug_status_new": "illegal_bug_status_transition",
    "field_required": "object_not_specified",
    "object_does_not_exist_user": "object_does_not_exist",
    "object_does_not_exist_field": "object_does_not_exist",
    "object_does_not_exist_field_id": "object_does_not_exist",
    "object_does_not_exist_value": "object_does_not_exist",
    "object_does_not_exist_in_product": "object_does_not_exist",
}


class BugzillaError(Exception):
    """A ThrowUserError/ThrowCodeError equivalent carrying the numeric code and rendered message."""

    def __init__(self, name: str, message: str, *, code: int | None = None, http_status: int | None = None):
        super().__init__(message)
        self.name = ERROR_NAME_ALIASES.get(name, name)
        self.message = message
        self.code = code if code is not None else WS_ERROR_CODE.get(self.name, 32000)
        self.http_status = http_status if http_status is not None else REST_STATUS_CODE_MAP.get(self.code, 400)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": True,
            "code": self.code,
            "message": self.message,
            "documentation": DOCUMENTATION_URL,
        }


def user_error(error_name: str, **vars: Any) -> BugzillaError:
    return BugzillaError(error_name, MESSAGES[error_name].format(**vars))


def param_required(param: str, function: str | None = None) -> BugzillaError:
    if function:
        msg = f"The function {function} requires a {param} argument, and that argument was not set."
    else:
        msg = f"The function requires a {param} argument, and that argument was not set."
    return BugzillaError("param_required", msg)


MESSAGES: dict[str, str] = {
    "invalid_field_name": "Can't use \"{field}\" as a field name.",
    "invalid_bug_id_or_alias": "'{bug_id}' is not a valid bug number nor an alias to a bug.",
    "invalid_bug_id_non_existent": "Bug #{bug_id} does not exist.",
    "bug_access_denied": "You are not authorized to access bug {bug_id}.",
    "comment_id_invalid": "{comment_id} is not a valid comment id.",
    "comment_is_private": "Comment #{comment_id} is private.",
    "missing_resolution": "A valid resolution is required to mark bugs as {status}.",
    "alias_in_use": "Bug {bug_id} has already taken the alias {alias}. Please choose another alias.",
    "alias_is_numeric": (
        "You tried to give this bug the alias {alias}, but aliases cannot be merely numbers, "
        "since they could then be confused with bug IDs. Please choose an alias containing at least one letter."
    ),
    "alias_too_long": "Bug aliases cannot be longer than 40 characters. Please choose a shorter alias.",
    "alias_has_comma_or_space": (
        "The alias you entered, {alias}, contains one or more commas or spaces. Aliases cannot "
        "contain commas or spaces because those characters are used to separate aliases from each other "
        "in lists. Please choose another alias that does not contain commas and spaces."
    ),
    "user_not_insider": "Sorry, but you are not allowed to (un)mark comments or attachments as private.",
    "entry_access_denied": (
        "Sorry, either the product {product} does not exist or you aren't authorized to enter a bug into it."
    ),
    "product_access_denied": (
        "Sorry, either the product {product} does not exist or you aren't authorized to access it."
    ),
    "auth_failure": (
        "Sorry, you aren't a member of the '{group}' group, and so you are not authorized to {reason} {object}."
    ),
    "api_key_not_valid": "The API key you specified is invalid. Please check that you typed it correctly.",
    "api_key_revoked": "The API key you specified has been revoked.",
    "invalid_content_type": (
        "The content type {contenttype} is invalid. Valid types must be of the form foo/bar where foo is "
        "one of application, audio, image, message, model, multipart, text, video and bar must not contain "
        "any special characters (such as \"=\", \"?\", ...)."
    ),
    "invalid_token": (
        "The cookies or token provide were not valid or have expired. You may login again to get new "
        "cookies or a new token."
    ),
    "zero_length_file": (
        "The file you are trying to attach is empty, does not exist, or you don't have permission to read it."
    ),
    "dependency_loop_single": "You can't make a bug block itself or depend on itself.",
    "dependency_loop_multi": (
        "The following bug(s) would appear on both the \"depends on\" and \"blocks\" parts of the "
        "dependency tree if these changes are committed: {bug_ids}. This would create a circular dependency, "
        "which is not allowed."
    ),
    "invalid_username_or_password": "The login or password you entered is not valid.",
    "invalid_username": (
        "The name {name} is not a valid username. Either you misspelled it, or the person has not "
        "registered for a Bugzilla account."
    ),
    "object_does_not_exist_user": (
        "There is no user named '{name}'. Either you mis-typed the name or that user has not yet "
        "registered for a Bugzilla account."
    ),
    "object_does_not_exist_field": "There is no field named '{name}'.",
    "object_does_not_exist_field_id": "There is no field with the id '{id}'.",
    "object_not_specified": "You must select/enter a {class}.",
    "invalid_attach_id": "The attachment id {attach_id} is invalid.",
    "illegal_attachment_edit": "You are not authorized to edit attachment {attach_id}.",
    "product_edit_denied": "You are not permitted to edit bugs in product {product}.",
    "bug_access_query": "You are not authorized to access bug {bug_id}. To see this bug, you must first log in to an account with the appropriate permissions.",
    "resolution_not_allowed": "You cannot set a resolution for open bugs.",
    "buglist_parameters_required": "You may not search, or create saved searches, without any search terms.",
    "filename_not_specified": "You must enter a filename for the attachment.",
    "freetext_too_long": "The text you entered in the {field} field is too long (more than 255 characters).",
    "object_does_not_exist_value": "There is no {class} named '{name}'.",
    "object_does_not_exist_in_product": "There is no {class} named '{name}' in the '{product}' product.",
    "illegal_bug_status_transition": "You are not allowed to change the bug status from {old} to {new}.",
    "illegal_bug_status_new": "You are not allowed to file new bugs with the {new} status.",
    "file_not_specified": "You did not specify a file to attach.",
    "missing_attachment_description": "You must enter a description for the attachment.",
    "require_summary": "You must enter a summary for this bug.",
    "login_required": "You must log in before using this part of Bugzilla.",
    "field_required": "You must select/enter a {field}.",
    "dupe_id_required": "You must specify a bug id to mark this bug as a duplicate of.",
    "rest_invalid_resource": "A REST API resource was not found for '{method} {path}'.",
    "comment_required": "You have to specify a comment when changing the {field} of a bug from {old} to {new}.",
    "illegal_change": "You tried to change the {field} field from {oldvalue} to {newvalue}, but only {privs} are allowed to change that field.",
    "comment_too_long": "Comments cannot be longer than 65535 characters.",
    "invalid_parameter": "The value '{value}' for the {name} parameter is invalid.",
    "number_not_numeric": "The value '{num}' in the {field} field is not a numeric value.",
    "dupe_not_allowed": "Bug {bug_id} cannot be marked as a duplicate of bug {dupe_of} as it is already a duplicate of bug {dupe_of}.",
    "missing_content_type": "You asked Bugzilla to auto-detect the content type, but your browser did not specify a content type when uploading the file. You have to enter a content type manually.",
    "file_too_large": "The file you are trying to attach is {filesize} kilobytes (KB) in size. Attachments cannot be more than {maxsize} KB.",
    "account_disabled": "The user account {disabled_reason} has been disabled.",
    "see_also_url_invalid": "'{url}' is not a valid URL to a bug.",
    "invalid_keyword_id": "There is no keyword named '{keyword}'.",
}
