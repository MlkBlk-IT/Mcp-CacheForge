#!/usr/bin/env node
// Lance la passerelle IcyCache (meme arguments que la CLI `icycache`).
//
//   npx icycache serve      # la gateway HTTP
//   npx icycache doctor     # diagnostic
//
// Un client MCP stdio possede stdout : rien ne doit y etre ecrit ici.

'use strict';

const { run } = require('./_launcher');

run(process.argv.slice(2));
