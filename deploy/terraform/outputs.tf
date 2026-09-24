output "ip" {
  description = "Reserved IP; set it as the DEPLOY_HOST repository variable."
  value       = digitalocean_reserved_ip.app.ip_address
}

output "sslip_domain" {
  description = "A hostname that resolves to the reserved IP, for DOMAIN in .env if you have no domain of your own."
  value       = "${replace(digitalocean_reserved_ip.app.ip_address, ".", "-")}.sslip.io"
}

output "ssh" {
  value = "ssh deploy@${digitalocean_reserved_ip.app.ip_address}"
}

output "known_hosts_command" {
  description = "Run once the Droplet is up; store the output as the DEPLOY_KNOWN_HOSTS secret."
  value       = "ssh-keyscan -t ed25519 ${digitalocean_reserved_ip.app.ip_address}"
}
