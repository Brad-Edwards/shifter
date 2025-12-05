---
applyTo: *.*
---

## Project Overview

Shifter is a cloud-hosted XDR/XSIAM demo and attack testing environment deployed to AWS. It is a fork of APTL (Advanced Purple Team Lab) adapted for PANW SecOps Domain Consultants. Unlike APTL's local Docker-based approach with Wazuh SIEM, Shifter uses AWS CloudFormation with XDR/XSIAM integration.

### Architecture

- **Windows Workstation (t3.xlarge)**: Cloud desktop with Cursor IDE and MCP servers for controlling remote boxes via SSH
- **Kali Attack Box (t3.medium)**: Headless Kali Linux for attack execution
- **Victim Linux (t3.medium)**: Target system with XDR/XSIAM agent

### Key Components

- `mcp/aptl-mcp-common/`: Core shared MCP library (TypeScript)
- `mcp/mcp-red/`: Example MCP server implementation for red team operations

## Build and Test Commands

### MCP Common Library

```bash
cd mcp/aptl-mcp-common
npm install
npm run build    # TypeScript compilation
npm test         # Run tests with Vitest
npm run test:watch  # Watch mode for tests
```

### MCP Red Server

```bash
cd mcp/mcp-red
npm install
npm run build    # TypeScript compilation
npm run lint     # ESLint code analysis
npm run lint:fix # Auto-fix lint issues
npm run format   # Prettier formatting
npm run format:check  # Check formatting
npm test         # Run tests with Vitest
npm run inspector  # Test with MCP Inspector
```

## Coding Conventions

### TypeScript/JavaScript

- Use ES modules (`"type": "module"` in package.json)
- Follow ESLint rules configured in `eslint.config.js`
- Use Prettier for code formatting
- Use Zod for runtime type validation
- Use Vitest for testing

### Writing Style

- Use plain, simple, direct language
- Write for busy technical professionals
- Be concise and to the point
- Focus on facts and functionality
- Avoid marketing language, boosterism, or sales-y content

### Workflow Guidelines

- Only do what is explicitly requested
- Ask for clarification rather than making assumptions
- Keep responses focused and concise
- Respect existing code patterns and structure

## Security Considerations

- Always review for secure coding best practices
- Ensure modern coding practices and patterns are used
- This lab enables AI agents to run actual penetration testing tools
- Container/instance security must be monitored closely
- IP whitelisting is used for RDP/SSH access
- Key-only SSH authentication on Linux instances
- Auto-shutdown reduces attack surface when not in use

## File Structure

```
shifter/
├── .github/
│   └── copilot-instructions.md  # This file
├── CLAUDE.md                    # Detailed project guidance
├── mcp/
│   ├── aptl-mcp-common/         # Core MCP library
│   │   ├── src/                 # TypeScript source
│   │   ├── tests/               # Vitest tests
│   │   ├── package.json
│   │   └── tsconfig.json
│   └── mcp-red/                 # Red team MCP server
│       ├── src/                 # TypeScript source
│       ├── package.json
│       ├── eslint.config.js
│       └── tsconfig.json
└── README.md
```

## Dependencies

- Node.js 20.x LTS
- TypeScript 5.x
- `@modelcontextprotocol/sdk` for MCP integration
- `ssh2` for remote command execution
- `zod` for schema validation
