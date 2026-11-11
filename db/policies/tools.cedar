// search_documents is retrieval behind the tool door: any role in its own org (same as org_read).
@id("search_tool_any_role")
permit (
  principal is HarnessRag::User,
  action == HarnessRag::Action::"tool:search_documents",
  resource is HarnessRag::Org
) when {
  resource.org == principal.org &&
  principal.roles.containsAny(["viewer", "analyst", "admin"])
};

// Read tools (facts_sql, calculator) need analyst or admin, in the caller's own org.
@id("read_tools_analyst_or_admin")
permit (
  principal is HarnessRag::User,
  action in HarnessRag::Action::"ReadTools",
  resource is HarnessRag::Org
) when {
  resource.org == principal.org &&
  principal.roles.containsAny(["analyst", "admin"])
};

// Write / money tools need admin. HITL approval and second-model verification
// are enforced by the harness on top of this decision, never instead of it.
@id("write_tools_admin")
permit (
  principal is HarnessRag::User,
  action in HarnessRag::Action::"WriteTools",
  resource is HarnessRag::Org
) when {
  resource.org == principal.org &&
  principal.roles.contains("admin")
};

// Belt and braces: an explicit forbid for anyone outside the resource's org.
@id("cross_org_forbid")
forbid (principal is HarnessRag::User, action, resource)
when { resource has org && resource.org != principal.org };
