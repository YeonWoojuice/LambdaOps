output "alarm_names" {
  description = "Names of the three dev CloudWatch alarms."
  value       = module.slash_m2_alarms.alarm_names
}

output "alarm_arns" {
  description = "Pass this exact M2 output to the M3 root through remote state."
  value       = module.slash_m2_alarms.alarm_arns
}
