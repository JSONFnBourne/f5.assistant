export type GeneratorInput = {
    tmosVersion: string;
    protocol: string;
    context: string;
    match: string;
    action: string;
    dependencies: string;
};

export type GeneratedIRule = {
    name: string;
    code: string;
    instructions: string;
    validationPlan: string;
    rollbackPlan: string;
};

/**
 * Generates an iRule based on structured input.
 * In a real expert system, this would use an LLM or a complex decision tree.
 * Here we provide a template-based implementation for common patterns.
 */
export function generateIRule(input: GeneratorInput): GeneratedIRule {
    const timestamp = new Date().toISOString().split('T')[0];
    const ruleName = `rule_generated_${timestamp.replace(/-/g, '')}`;

    let event = 'CLIENT_ACCEPTED';
    if (input.protocol.toUpperCase().includes('HTTP')) {
        event = 'HTTP_REQUEST';
    }

    const code = `# Name: ${ruleName}
# Purpose: Generated iRule for ${input.action || 'Custom Logic'}
# Context: ${input.context}
# TMOS: ${input.tmosVersion}

when RULE_INIT {
    # Debug Logging Flag (0=off, 1=on)
    set static::debug 1
}

when ${event} {
    # Match Logic: ${input.match || 'Always match'}
    # Action Logic: ${input.action || 'Default action'}
    
    # Generated placeholder logic
    if { $static::debug } {
        log local0. "${ruleName}: Event ${event} triggered from [IP::client_addr]"
    }
    
    # [Insert Match/Action Code Here]
    # Example:
    # if { [HTTP::uri] starts_with "/admin" } {
    #     pool pool_admin_secure
    # }
}
`;

    return {
        name: ruleName,
        code,
        instructions: `1. Create the iRule named "${ruleName}" in the F5 UI or via TMSH.\n2. Attach to Virtual Server: ${input.context}.\n3. Ensure Profile "${input.protocol.includes('HTTP') ? 'http' : 'tcp'}" is assigned.`,
        validationPlan: `1. Tail the logs: command "tail -f /var/log/ltm | grep ${ruleName}".\n2. Send test traffic matching: ${input.match}.\n3. Verify log entry appears and action is taken.`,
        rollbackPlan: `Remove the iRule from the Virtual Server configuration.`
    };
}
