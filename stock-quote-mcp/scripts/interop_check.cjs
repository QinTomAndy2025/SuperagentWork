#!/usr/bin/env node
/*
 * Interoperability check with the official Model Context Protocol TypeScript
 * SDK. This file is not required to run the server; it proves that the
 * hand-written stdio implementation speaks the protocol correctly.
 *
 * Usage (tested with @modelcontextprotocol/sdk 1.32.2):
 *
 *   mkdir -p /tmp/mcp-check && cd /tmp/mcp-check
 *   npm install @modelcontextprotocol/sdk
 *   NODE_PATH=/tmp/mcp-check/node_modules \
 *     STOCK_QUOTE_PROJECT=/abs/path/stock-quote-mcp \
 *     node /abs/path/stock-quote-mcp/scripts/interop_check.cjs
 */

const path = require("path");
const { Client } = require("@modelcontextprotocol/sdk/client/index.js");
const { StdioClientTransport } = require("@modelcontextprotocol/sdk/client/stdio.js");

const project = process.env.STOCK_QUOTE_PROJECT || process.cwd();

(async () => {
  const transport = new StdioClientTransport({
    command: "python3",
    args: ["-m", "stock_quote_mcp"],
    cwd: project,
    env: { ...process.env, PYTHONPATH: path.join(project, "src") },
    stderr: "pipe",
  });
  const client = new Client({ name: "interop-check", version: "1.0.0" });
  await client.connect(transport);

  console.log("server:", JSON.stringify(client.getServerVersion()));
  const tools = await client.listTools();
  console.log("tools:", tools.tools.map((tool) => tool.name).join(", "));

  const quotes = await client.callTool({
    name: "get_realtime_quotes",
    arguments: { symbols: ["600519", "sh000001"] },
  });
  const quotePayload = JSON.parse(quotes.content[0].text);
  console.log(
    "quotes:",
    quotePayload.source,
    quotePayload.quotes.length,
    quotePayload.quotes[0].symbol,
    quotePayload.quotes[0].price,
    quotePayload.quotes[0].timestamp,
  );
  console.log("structuredContent:", Boolean(quotes.structuredContent), "| isError:", quotes.isError);

  const minutes = await client.callTool({
    name: "get_intraday_minutes",
    arguments: { symbol: "600519" },
  });
  const minutePayload = JSON.parse(minutes.content[0].text);
  console.log("minutes:", minutePayload.date, minutePayload.count, minutePayload.bars[0].time);

  const bad = await client.callTool({
    name: "get_realtime_quotes",
    arguments: { symbols: ["not-a-code"] },
  });
  console.log("bad-symbol isError:", bad.isError);

  await client.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
