# Amazon Bedrock Guardrail applied to every Converse call and to both ends of a turn (ApplyGuardrail).
# In production: this guardrail (content filters, anonymized PII, a denied "investment advice" topic)
#   runs on every model call; the API reads its id and version from BEDROCK_GUARDRAIL_ID / _VERSION.
# In this demo: the compose stack runs HR_MODEL_BACKEND=fake with no guardrail id, so only the
#   deterministic checks in services/finance_agents/guardrails run, because there is no AWS locally.
resource "aws_bedrock_guardrail" "this" {
  name                      = var.project
  description               = "Content, PII and topic guardrail for the ${var.project} API"
  blocked_input_messaging   = "I can't help with that request. Ask about the content of SEC filings you have access to."
  blocked_outputs_messaging = "The answer was withheld by a content guardrail. Try rephrasing your question about the filings."

  content_policy_config {
    dynamic "filters_config" {
      for_each = toset(["HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT"])
      content {
        type            = filters_config.value
        input_strength  = "HIGH"
        output_strength = "HIGH"
      }
    }
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE" # Bedrock requires NONE for PROMPT_ATTACK on the output side
    }
  }

  sensitive_information_policy_config {
    dynamic "pii_entities_config" {
      for_each = toset(["EMAIL", "PHONE", "US_SOCIAL_SECURITY_NUMBER", "CREDIT_DEBIT_CARD_NUMBER"])
      content {
        type   = pii_entities_config.value
        action = "ANONYMIZE"
      }
    }
  }

  topic_policy_config {
    topics_config {
      name       = "investment-advice"
      type       = "DENY"
      definition = "Recommendations to buy, sell or hold a security, portfolio allocation advice, or price predictions, as opposed to reporting what a filing states."
      examples   = ["Should I buy AAPL stock?", "Is MSFT a good investment right now?", "What will NVDA's share price be next year?"]
    }
  }
}

resource "aws_bedrock_guardrail_version" "this" {
  guardrail_arn = aws_bedrock_guardrail.this.guardrail_arn
  description   = "Managed by Terraform"
}
