variable "region" {
  description = "DigitalOcean region; ams3 keeps address-level data in the Netherlands."
  type        = string
  default     = "ams3"
}

variable "size" {
  description = "Droplet size. 1 vCPU / 1 GiB covers the nightly job for a 50-listing watchlist."
  type        = string
  default     = "s-1vcpu-1gb"
}

variable "admin_public_key_path" {
  description = "Your SSH public key, for root access."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "deploy_public_key" {
  description = "Public half of the key the GitHub Deploy workflow uses (user 'deploy')."
  type        = string
}

variable "ssh_allowed_cidrs" {
  description = "Who may reach port 22. GitHub-hosted runners use changing IPs, so the deploy job needs 0.0.0.0/0 unless you deploy by hand."
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}

variable "alert_email" {
  description = "Receives DigitalOcean monitoring alerts (disk filling up)."
  type        = string
}
