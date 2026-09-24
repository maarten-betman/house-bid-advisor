resource "digitalocean_ssh_key" "admin" {
  name       = "bidadvisor-admin"
  public_key = file(pathexpand(var.admin_public_key_path))
}

resource "digitalocean_droplet" "app" {
  name       = "bidadvisor"
  region     = var.region
  size       = var.size
  image      = "ubuntu-24-04-x64"
  ssh_keys   = [digitalocean_ssh_key.admin.fingerprint]
  backups    = true # weekly snapshots; the lake lives on this disk
  monitoring = true
  tags       = ["bidadvisor"]

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    deploy_public_key = var.deploy_public_key
    nightly_service   = file("${path.module}/../systemd/bidadvisor-nightly.service")
    nightly_timer     = file("${path.module}/../systemd/bidadvisor-nightly.timer")
  })

  lifecycle {
    # cloud-init only runs at first boot; editing it must not rebuild the Droplet and its data.
    ignore_changes = [user_data]
  }
}

resource "digitalocean_reserved_ip" "app" {
  region     = var.region
  droplet_id = digitalocean_droplet.app.id
}

resource "digitalocean_firewall" "app" {
  name        = "bidadvisor"
  droplet_ids = [digitalocean_droplet.app.id]

  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = var.ssh_allowed_cidrs
  }

  inbound_rule {
    protocol         = "icmp"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
}

resource "digitalocean_monitor_alert" "disk" {
  type        = "v1/insights/droplet/disk_utilization_percent"
  compare     = "GreaterThan"
  value       = 80
  window      = "10m"
  entities    = [digitalocean_droplet.app.id]
  description = "bidadvisor disk above 80%"
  enabled     = true

  alerts {
    email = [var.alert_email]
  }
}

resource "digitalocean_project" "app" {
  name        = "house-bid-advisor"
  description = "Personal bid advisor: nightly Funda watchlist and Kadaster models"
  purpose     = "Web Application"
  environment = "Production"
  resources = [
    digitalocean_droplet.app.urn,
    digitalocean_reserved_ip.app.urn,
  ]
}
