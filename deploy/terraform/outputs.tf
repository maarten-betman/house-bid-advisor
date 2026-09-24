output "ip" {
  description = "Reserved IP; set it as the DEPLOY_HOST repository variable."
  value       = digitalocean_reserved_ip.app.ip_address
}

output "ssh" {
  value = "ssh deploy@${digitalocean_reserved_ip.app.ip_address}"
}

output "known_hosts_command" {
  description = "Run once the Droplet is up; store the output as the DEPLOY_KNOWN_HOSTS secret."
  value       = "ssh-keyscan -t ed25519 ${digitalocean_reserved_ip.app.ip_address}"
}
