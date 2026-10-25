#!/usr/bin/env node
// Serveur MCP IcyCache sur stdio - a pointer dans la config d'un client MCP :
//
//   { "mcpServers": { "icycache": { "command": "npx", "args": ["-y", "icycache-mcp"] } } }
//
// stdout appartient au JSON-RPC : le wrapper n'y ecrit rien.

'use strict';

const { run } = require('./_launcher');

run(['mcp', '--mode', 'mcp-stdio']);
