output "bucket" {
  value = aws_s3_bucket.web.bucket
}

output "url" {
  value = "https://${aws_cloudfront_distribution.web.domain_name}"
}
