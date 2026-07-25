# Range VPC Firewall Routing
#
# Subnet and route tables that steer range egress through AWS Network Firewall.
# Rule groups live in firewall_rules_domains.tf and firewall_rules_ips.tf; the
# policy, firewall, and logging wiring live in firewall.tf.

# ------------------------------------------------------------------------------
# Firewall Subnet (10.1.0.0/28)
# ------------------------------------------------------------------------------

resource "aws_subnet" "firewall" {
  count = var.enable_network_firewall ? 1 : 0

  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 12, 0) # 10.1.0.0/28
  availability_zone       = local.primary_az
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-firewall-subnet"
    Tier = "firewall"
  })
}

# ------------------------------------------------------------------------------
# Firewall Route Table
# ------------------------------------------------------------------------------

resource "aws_route_table" "firewall" {
  count = var.enable_network_firewall ? 1 : 0

  vpc_id = aws_vpc.this.id

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-firewall-rt"
  })
}

# Traffic from firewall goes to NAT Gateway
resource "aws_route" "firewall_to_nat" {
  count = var.enable_network_firewall ? 1 : 0

  route_table_id         = aws_route_table.firewall[0].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this.id
}

resource "aws_route_table_association" "firewall" {
  count = var.enable_network_firewall ? 1 : 0

  subnet_id      = aws_subnet.firewall[0].id
  route_table_id = aws_route_table.firewall[0].id
}

# ------------------------------------------------------------------------------
# Private Route Table (for user subnets)
# ------------------------------------------------------------------------------

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-private-rt"
  })
}

# Route to firewall when enabled, otherwise to NAT directly
resource "aws_route" "private_to_firewall" {
  count = var.enable_network_firewall ? 1 : 0

  route_table_id         = aws_route_table.private.id
  destination_cidr_block = "0.0.0.0/0"
  vpc_endpoint_id        = one(one(aws_networkfirewall_firewall.this[0].firewall_status).sync_states).attachment[0].endpoint_id
}

# Fallback route to NAT when firewall is disabled
resource "aws_route" "private_to_nat" {
  count = var.enable_network_firewall ? 0 : 1

  route_table_id         = aws_route_table.private.id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this.id
}
