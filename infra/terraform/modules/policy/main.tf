# Amazon Verified Permissions store holding the same policies the API evaluates in process.
# In production: HR_AUTHZ_BACKEND=verified_permissions and every decision is an IsAuthorized call
#   against this store, audited by CloudTrail.
# In this demo: cedar_local evaluates db/policies/*.cedar in process by default, because a local
#   docker compose has no AWS; this store keeps both paths loading the identical policy text.
resource "aws_verifiedpermissions_policy_store" "this" {
  description = var.project
  validation_settings {
    mode = "STRICT"
  }
}

# AVP's PutSchema API accepts only the JSON spelling of a Cedar schema, so schema.json is the
# hand-translated twin of db/policies/schema.cedarschema (keep them in step).
resource "aws_verifiedpermissions_schema" "this" {
  policy_store_id = aws_verifiedpermissions_policy_store.this.id
  definition {
    value = file("${path.module}/schema.json")
  }
}

# One AVP static policy per `@id(...)` block. AVP takes exactly one policy per CreatePolicy, and
# the .cedar files hold several each, so the files are split on the `@id(` marker; the annotation
# itself is dropped (its name becomes the AVP description) because AVP's acceptance of Cedar
# annotations is not something this repo has verified.
locals {
  cedar_files = fileset(var.policies_dir, "*.cedar")
  policies = merge([
    for f in local.cedar_files : {
      for chunk in slice(split("@id(", file("${var.policies_dir}/${f}")), 1, length(split("@id(", file("${var.policies_dir}/${f}")))) :
      regex("^\"([a-z_]+)\"", chunk)[0] => trimspace(regex("(?s)^\"[a-z_]+\"\\)(.*)$", chunk)[0])
    }
  ]...)
}

resource "aws_verifiedpermissions_policy" "static" {
  for_each        = local.policies
  policy_store_id = aws_verifiedpermissions_policy_store.this.id
  definition {
    static {
      description = each.key
      statement   = each.value
    }
  }
  depends_on = [aws_verifiedpermissions_schema.this]
}
