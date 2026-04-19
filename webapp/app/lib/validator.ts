export type ValidationResult = {
    isValid: boolean;
    errors: string[];
    warnings: string[];
    profilesRequired: string[];
};

/**
 * Validates F5 iRule code against best practices and safety requirements.
 * Ref: f5-irules-generator-agent-instructions.md
 */
export function validateIRule(code: string): ValidationResult {
    const errors: string[] = [];
    const warnings: string[] = [];
    const profilesRequired: Set<string> = new Set();

    // Normalize code for analysis
    const lines = code.split('\n');
    const codeLower = code.toLowerCase();

    // 1. Check for Event Legality & Context
    // ----------------------------------------------------------------
    const hasHttpRequest = /when\s+http_request\s+\{/.test(codeLower);
    const hasHttpResponse = /when\s+http_response\s+\{/.test(codeLower);
    const hasClientAccepted = /when\s+client_accepted\s+\{/.test(codeLower);

    // Rule: HTTP commands should generally be in HTTP events
    if (codeLower.includes('http::') && (!hasHttpRequest && !hasHttpResponse)) {
        warnings.push('HTTP commands detected but no HTTP_REQUEST or HTTP_RESPONSE event found. Ensure this logic is placed in the correct event context.');
        profilesRequired.add('http');
    }

    // Rule: Avoid HTTP actions in CLIENT_ACCEPTED
    // This is a naive check; a real parser would check scope depth. 
    // We'll check if both exist and warn generally.
    if (hasClientAccepted && codeLower.includes('http::')) {
        warnings.push('Avoid using HTTP commands inside CLIENT_ACCEPTED. Use HTTP_REQUEST for HTTP headers/path logic.');
    }

    // 2. Profile Requirements
    // ----------------------------------------------------------------
    if (codeLower.includes('http::')) {
        profilesRequired.add('http');
    }
    if (codeLower.includes('ssl::')) {
        profilesRequired.add('clientssl or serverssl');
    }
    if (codeLower.includes('persist ')) {
        profilesRequired.add('persistence');
    }

    // 3. Performance & Safety Checks
    // ----------------------------------------------------------------

    // Rule: Prefer static:: variables over global variables
    // Check for 'set global_var' vs 'set static::global_var'
    // This is hard to detect perfectly with regex, but we can look for '::' usage that isn't static
    // Warn on `regexp`
    if (codeLower.includes('regexp')) {
        warnings.push('Performance Warning: "regexp" is expensive. Prefer "string map", "string range", or "switch -glob" where possible.');
    }

    // Rule: Avoid global arrays/tables if possible or warn about memory
    if (codeLower.includes('table set') || codeLower.includes('table add')) {
        warnings.push('State Tracking: Use of "table" command detected. Ensure appropriate timeouts are set to avoid memory exhaustion.');
    }

    // 4. Critical Logic Checks
    // ----------------------------------------------------------------
    const openBraces = (code.match(/\{/g) || []).length;
    const closeBraces = (code.match(/\}/g) || []).length;
    if (openBraces !== closeBraces) {
        errors.push(`Syntax Error: Mismatched braces. Open: ${openBraces}, Close: ${closeBraces}.`);
    }

    return {
        isValid: errors.length === 0,
        errors,
        warnings,
        profilesRequired: Array.from(profilesRequired)
    };
}
