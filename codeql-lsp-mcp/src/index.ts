import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
    CallToolRequestSchema,
    ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { CodeQLLanguageServer } from "./codeql-lsp-client.js";
import { execSync, execFileSync } from "child_process";
import { randomInt } from "crypto";
import { hostname } from "os";
import { existsSync } from "fs";
import { join, delimiter } from "path";

const instanceId = `${hostname()}-${process.pid}-${randomInt(10000)}`;

const server = new Server(
    {
        name: "mcp-codeql-server",
        version: "0.1.0",
    },
    {
        capabilities: {
            tools: {},
        },
    }
);

let codeqlServer: CodeQLLanguageServer | null = null;

// Initialize server when first needed
async function ensureCodeQLServer() {
    if (!codeqlServer) {
        console.error(`[${instanceId}] Creating new CodeQL server instance...`);
        codeqlServer = new CodeQLLanguageServer({ verbose: false });
        console.error(`[${instanceId}] Starting CodeQL server with workspace: ${process.cwd()}`);
        await codeqlServer.start([process.cwd()]);
        console.error(`[${instanceId}] CodeQL language server started and ready`);
    }
    return codeqlServer;
}

server.setRequestHandler(ListToolsRequestSchema, async () => {
    return {
        tools: [
            {
                name: "codeql_complete",
                description: "Get code completions at a specific position in a CodeQL file",
                inputSchema: {
                    type: "object",
                    properties: {
                        file_uri: {
                            type: "string",
                            description: "The URI of the CodeQL file",
                        },
                        line: {
                            type: "number",
                            description: "Line number (0-based)",
                        },
                        character: {
                            type: "number",
                            description: "Character position in the line (0-based)",
                        },
                        trigger_character: {
                            type: "string",
                            description: "Optional trigger character",
                        },
                        limit: {
                            type: "number",
                            description: "Maximum number of completion items to return (default: 50)",
                        },
                        offset: {
                            type: "number",
                            description: "Starting position for pagination (default: 0)",
                        },
                    },
                    required: ["file_uri", "line", "character"],
                },
            },
            {
                name: "codeql_hover",
                description: "Get hover information at a specific position",
                inputSchema: {
                    type: "object",
                    properties: {
                        file_uri: {
                            type: "string",
                            description: "The URI of the CodeQL file",
                        },
                        line: {
                            type: "number",
                            description: "Line number (0-based)",
                        },
                        character: {
                            type: "number",
                            description: "Character position in the line (0-based)",
                        },
                    },
                    required: ["file_uri", "line", "character"],
                },
            },
            {
                name: "codeql_definition",
                description: "Go to definition for a symbol at a specific position",
                inputSchema: {
                    type: "object",
                    properties: {
                        file_uri: {
                            type: "string",
                            description: "The URI of the CodeQL file",
                        },
                        line: {
                            type: "number",
                            description: "Line number (0-based)",
                        },
                        character: {
                            type: "number",
                            description: "Character position in the line (0-based)",
                        },
                    },
                    required: ["file_uri", "line", "character"],
                },
            },
            {
                name: "codeql_diagnostics",
                description: "Get diagnostics (errors, warnings) for a file",
                inputSchema: {
                    type: "object",
                    properties: {
                        file_uri: {
                            type: "string",
                            description: "The URI of the CodeQL file",
                        },
                    },
                    required: ["file_uri"],
                },
            },
            {
                name: "codeql_format",
                description: "Format a CodeQL file or selection",
                inputSchema: {
                    type: "object",
                    properties: {
                        file_uri: {
                            type: "string",
                            description: "The URI of the CodeQL file",
                        },
                        range: {
                            type: "object",
                            description: "Optional range to format",
                            properties: {
                                start: {
                                    type: "object",
                                    properties: {
                                        line: { type: "number" },
                                        character: { type: "number" },
                                    },
                                    required: ["line", "character"],
                                },
                                end: {
                                    type: "object",
                                    properties: {
                                        line: { type: "number" },
                                        character: { type: "number" },
                                    },
                                    required: ["line", "character"],
                                },
                            },
                            required: ["start", "end"],
                        },
                    },
                    required: ["file_uri"],
                },
            },
            {
                name: "codeql_references",
                description: "Find all references to a symbol at a specific position",
                inputSchema: {
                    type: "object",
                    properties: {
                        file_uri: {
                            type: "string",
                            description: "The URI of the CodeQL file",
                        },
                        line: {
                            type: "number",
                            description: "Line number (0-based)",
                        },
                        character: {
                            type: "number",
                            description: "Character position in the line (0-based)",
                        },
                    },
                    required: ["file_uri", "line", "character"],
                },
            },
            {
                name: "codeql_open_file",
                description: "Open a CodeQL file in the language server",
                inputSchema: {
                    type: "object",
                    properties: {
                        file_uri: {
                            type: "string",
                            description: "The URI of the CodeQL file",
                        },
                        content: {
                            type: "string",
                            description: "The content of the file",
                        },
                    },
                    required: ["file_uri", "content"],
                },
            },
            {
                name: "codeql_update_file",
                description: "Update the content of an open CodeQL file",
                inputSchema: {
                    type: "object",
                    properties: {
                        file_uri: {
                            type: "string",
                            description: "The URI of the CodeQL file",
                        },
                        content: {
                            type: "string",
                            description: "The new content of the file",
                        },
                    },
                    required: ["file_uri", "content"],
                },
            },
            {
                name: "codeql_set_workspace",
                description: "Set workspace folders for better CodeQL analysis",
                inputSchema: {
                    type: "object",
                    properties: {
                        folders: {
                            type: "array",
                            description: "Array of folder paths to use as workspace folders",
                            items: {
                                type: "string",
                            },
                        },
                    },
                    required: ["folders"],
                },
            },
            {
                name: "codeql_compile",
                description: "Compile-check a CodeQL query without running it. Faster than codeql_run_query for catching syntax and type errors.",
                inputSchema: {
                    type: "object",
                    properties: {
                        query_path: {
                            type: "string",
                            description: "Absolute path to the .ql query file",
                        },
                    },
                    required: ["query_path"],
                },
            },
            {
                name: "codeql_run_query",
                description: "Run a CodeQL query against a database and return results. " +
                    "For FN/TP cases the query should produce results (vulnerability detected). " +
                    "For FP/TN cases the query should produce no results (safe code, no false alarm).",
                inputSchema: {
                    type: "object",
                    properties: {
                        query_path: {
                            type: "string",
                            description: "Absolute path to the .ql query file",
                        },
                        db_path: {
                            type: "string",
                            description: "Absolute path to the CodeQL database directory",
                        },
                        label: {
                            type: "string",
                            enum: ["fn", "fp", "tp", "tn"],
                            description: "Expected label: fn/tp = should detect (results expected), fp/tn = should not detect (no results expected)",
                        },
                    },
                    required: ["query_path", "db_path"],
                },
            },
        ],
    };
});

server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;

    if (!args) {
        return {
            content: [
                {
                    type: "text",
                    text: "Missing arguments",
                },
            ],
        };
    }

    try {
        codeqlServer = await ensureCodeQLServer();
    } catch (error) {
        return {
            content: [
                {
                    type: "text",
                    text: `Failed to start CodeQL language server: ${error instanceof Error ? error.message : String(error)}\n\nPlease ensure CodeQL CLI is installed and accessible.`,
                },
            ],
        };
    }

    try {
        switch (name) {
            case "codeql_complete": {
                const completions = await codeqlServer!.getCompletions(
                    args.file_uri as string,
                    args.line as number,
                    args.character as number,
                    args.trigger_character as string | undefined
                );

                const offset = args.offset as number || 0;
                const limit = args.limit as number || 50;
                const totalItems = completions.items?.length || 0;
                const items = completions.items?.slice(offset, offset + limit) || [];

                const paginatedCompletions = {
                    ...completions,
                    items,
                    pagination: {
                        offset,
                        limit,
                        total: totalItems,
                        hasMore: offset + limit < totalItems
                    }
                };

                let responseText = JSON.stringify(paginatedCompletions);
                if (totalItems > limit) {
                    const nextOffset = offset + limit;
                    const remaining = totalItems - nextOffset;
                    if (remaining > 0) {
                        responseText += `\n\n[PAGINATION INFO: Showing ${items.length} of ${totalItems} items. To get next ${Math.min(remaining, limit)} items, use: offset=${nextOffset}, limit=${limit}]`;
                    }
                }

                return {
                    content: [
                        {
                            type: "text",
                            text: responseText,
                        },
                    ],
                };
            }

            case "codeql_hover": {
                const hover = await codeqlServer!.getHover(
                    args.file_uri as string,
                    args.line as number,
                    args.character as number
                );
                return {
                    content: [
                        {
                            type: "text",
                            text: JSON.stringify(hover, null, 2),
                        },
                    ],
                };
            }

            case "codeql_definition": {
                const definition = await codeqlServer!.getDefinition(
                    args.file_uri as string,
                    args.line as number,
                    args.character as number
                );
                return {
                    content: [
                        {
                            type: "text",
                            text: JSON.stringify(definition, null, 2),
                        },
                    ],
                };
            }

            case "codeql_diagnostics": {
                const diagnostics = await codeqlServer!.getDiagnostics(
                    args.file_uri as string
                );
                return {
                    content: [
                        {
                            type: "text",
                            text: JSON.stringify(diagnostics, null, 2),
                        },
                    ],
                };
            }

            case "codeql_format": {
                const edits = await codeqlServer!.formatDocument(
                    args.file_uri as string,
                    args.range as any
                );
                return {
                    content: [
                        {
                            type: "text",
                            text: JSON.stringify(edits, null, 2),
                        },
                    ],
                };
            }

            case "codeql_references": {
                const references = await codeqlServer!.getReferences(
                    args.file_uri as string,
                    args.line as number,
                    args.character as number
                );
                return {
                    content: [
                        {
                            type: "text",
                            text: JSON.stringify(references, null, 2),
                        },
                    ],
                };
            }

            case "codeql_open_file": {
                await codeqlServer!.openDocument(
                    args.file_uri as string,
                    args.content as string
                );
                return {
                    content: [
                        {
                            type: "text",
                            text: "File opened successfully",
                        },
                    ],
                };
            }

            case "codeql_update_file": {
                await codeqlServer!.updateDocument(
                    args.file_uri as string,
                    args.content as string
                );
                return {
                    content: [
                        {
                            type: "text",
                            text: "File updated successfully",
                        },
                    ],
                };
            }

            case "codeql_set_workspace": {
                const folders = args.folders as string[];

                await codeqlServer!.setWorkspaceFolders(folders);

                return {
                    content: [
                        {
                            type: "text",
                            text: `Workspace folders set: ${folders.join(", ")}`,
                        },
                    ],
                };
            }

            case "codeql_compile": {
                const queryPath = args.query_path as string;

                if (!existsSync(queryPath)) {
                    return { content: [{ type: "text", text: `Error: query file not found: ${queryPath}` }] };
                }

                const codeqlPath = process.env.CODEQL_PATH ||
                    process.env.PATH?.split(delimiter).map(d => join(d, "codeql")).find(p => existsSync(p)) ||
                    "codeql";
                const searchPath = process.env.CODEQL_SEARCH_PATH;

                const cmd = [codeqlPath, "query", "compile", "--check-only", queryPath];
                if (searchPath) cmd.push("--search-path", searchPath);

                try {
                    execFileSync(cmd[0], cmd.slice(1), { timeout: 120000, encoding: "utf8" });
                    return { content: [{ type: "text", text: "Compilation successful — no errors." }] };
                } catch (e: any) {
                    const stderr = e.stderr?.toString() || String(e);
                    return { content: [{ type: "text", text: `Compilation failed:\n${stderr}` }] };
                }
            }

            case "codeql_run_query": {
                const queryPath = args.query_path as string;
                const dbPath = args.db_path as string;
                const label = args.label as string | undefined;

                if (!existsSync(queryPath)) {
                    return { content: [{ type: "text", text: `Error: query file not found: ${queryPath}` }] };
                }
                if (!existsSync(dbPath)) {
                    return { content: [{ type: "text", text: `Error: database not found: ${dbPath}` }] };
                }

                const codeqlPath = process.env.CODEQL_PATH ||
                    process.env.PATH?.split(delimiter).map(d => join(d, "codeql")).find(p => existsSync(p)) ||
                    "codeql";
                const searchPath = process.env.CODEQL_SEARCH_PATH;

                const cmd = [codeqlPath, "query", "run", queryPath, "--database", dbPath];
                if (searchPath) cmd.push("--search-path", searchPath);

                let stdout = "";
                let stderr = "";
                let success = false;
                try {
                    stdout = execFileSync(cmd[0], cmd.slice(1), { timeout: 300000, encoding: "utf8" });
                    success = true;
                } catch (e: any) {
                    stderr = e.stderr?.toString() || String(e);
                    stdout = e.stdout?.toString() || "";
                }

                if (!success) {
                    return { content: [{ type: "text", text: `Query execution failed:\n${stderr}` }] };
                }

                // Parse result locations (file://...path:line:col:line:col)
                const resultLines: string[] = [];
                for (const line of stdout.split("\n")) {
                    if (line.includes("file://") || (line.trim() && !line.startsWith("Running"))) {
                        resultLines.push(line);
                    }
                }

                const hasResults = !stdout.includes("No results found") &&
                    resultLines.some(l => l.includes("file://"));

                // Verdict based on label
                let verdict = "";
                if (label) {
                    const expectResults = label === "fn" || label === "tp";
                    if (expectResults && hasResults) {
                        verdict = "\nVERDICT: PASS — query correctly detects the vulnerability.";
                    } else if (expectResults && !hasResults) {
                        verdict = "\nVERDICT: FAIL — query misses the vulnerability (false negative). Fix needed.";
                    } else if (!expectResults && !hasResults) {
                        verdict = "\nVERDICT: PASS — query correctly produces no results on safe code.";
                    } else {
                        verdict = "\nVERDICT: FAIL — query falsely flags safe code (false positive). Fix needed.";
                    }
                }

                const output = resultLines.length > 0
                    ? `Results:\n${resultLines.join("\n")}`
                    : "No results found.";

                return { content: [{ type: "text", text: output + verdict }] };
            }

            default:
                throw new Error(`Unknown tool: ${name}`);
        }
    } catch (error) {
        return {
            content: [
                {
                    type: "text",
                    text: `Error: ${error instanceof Error ? error.message : String(error)}`,
                },
            ],
        };
    }
});

// Cleanup on exit
const cleanup = async () => {
    if (codeqlServer) {
        console.error("Stopping CodeQL language server...");
        await codeqlServer.stop();
    }
    process.exit(0);
};

process.on("SIGINT", () => { console.error("[SIGINT received]"); cleanup(); });
process.on("SIGTERM", () => { console.error("[SIGTERM received]"); cleanup(); });

const transport = new StdioServerTransport();
server.connect(transport);
console.error("CodeQL MCP Server started");
console.error("To use this server, set CODEQL_PATH environment variable or ensure 'codeql' is in PATH");

// Start CodeQL server eagerly to avoid cold start delay on first request
ensureCodeQLServer().then(() => {
    console.error("CodeQL language server ready");
}).catch((err) => {
    console.error(`Failed to pre-start CodeQL server: ${err}`);
});