import { CompletionList, Hover, Location, TextEdit, Diagnostic } from "vscode-languageserver-protocol";
export interface CodeQLClientOptions {
    codeqlPath?: string;
    verbose?: boolean;
}
export declare class CodeQLLanguageServer {
    private process;
    private connection;
    private documentVersions;
    private workspaceFolders;
    private readonly instanceId;
    private readonly logFilePath;
    private readonly verbose;
    private codeqlPath?;
    private diagnosticsListeners;
    private diagnosticsCache;
    constructor(options?: CodeQLClientOptions);
    private log;
    private findCodeQLPath;
    private isCodeQLAvailable;
    start(workspaceFolders?: string[]): Promise<void>;
    stop(): Promise<void>;
    openDocument(uri: string, content: string): Promise<void>;
    updateDocument(uri: string, content: string): Promise<void>;
    getCompletions(uri: string, line: number, character: number, triggerCharacter?: string): Promise<CompletionList>;
    getHover(uri: string, line: number, character: number): Promise<Hover | null>;
    getDefinition(uri: string, line: number, character: number): Promise<Location | Location[] | null>;
    /**
     * Get diagnostics for a document.
     * Waits for the publishDiagnostics notification from the language server.
     */
    getDiagnostics(uri: string, timeoutMs?: number): Promise<Diagnostic[]>;
    formatDocument(uri: string, range?: any): Promise<TextEdit[]>;
    getReferences(uri: string, line: number, character: number): Promise<Location[] | null>;
    /**
     * Update workspace folders without restarting the server.
     * Sends workspace/didChangeWorkspaceFolders notification to the LSP.
     */
    setWorkspaceFolders(folders: string[]): Promise<void>;
    getLogFilePath(): string | null;
    isRunning(): boolean;
}
//# sourceMappingURL=codeql-lsp-client.d.ts.map