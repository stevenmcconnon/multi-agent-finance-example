// Deny by default: Cedar permits nothing without a matching permit.
// A viewer, analyst or admin may retrieve/answer on a document in their own org
// when they, or any group they belong to, are in the document's ACL (resource.readers).
@id("document_acl_read")
permit (
  principal is FinanceAgents::User,
  action in [FinanceAgents::Action::"retrieve", FinanceAgents::Action::"answer"],
  resource is FinanceAgents::Document
) when {
  resource.org == principal.org &&
  principal.roles.containsAny(["viewer", "analyst", "admin"]) &&
  resource.readers.containsAny(principal.acl_ids)
};

// Org-wide retrieve/answer (the request before any document is known): any role in its own org.
// The row-level ACL predicate in retrieval/hybrid.py still filters every candidate.
@id("org_read")
permit (
  principal is FinanceAgents::User,
  action in [FinanceAgents::Action::"retrieve", FinanceAgents::Action::"answer"],
  resource is FinanceAgents::Org
) when {
  resource.org == principal.org &&
  principal.roles.containsAny(["viewer", "analyst", "admin"])
};
