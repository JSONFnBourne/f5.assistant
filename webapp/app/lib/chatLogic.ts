export type Message = {
    role: 'user' | 'assistant';
    content: string;
    timestamp: number;
};

/**
 * Simulates AI analysis of an iRule conversation.
 */
export function processChat(messages: Message[]): Message {
    const lastUserMsg = messages[messages.length - 1];
    const text = lastUserMsg.content.toLowerCase();

    let response = "I'm not sure how to answer that yet. Try asking me to explain an iRule code block.";

    if (text.includes('explain') || text.includes('what does') || text.includes('tell me about') || text.includes('analyze')) {
        response = `This appears to be an iRule related to traffic management. \n\n**Analysis**:\n1. **Event Trigger**: The code likely initiates on \`HTTP_REQUEST\` or similar.\n2. **Logic**: It executes conditional checks on headers or URIs.\n3. **Outcome**: It modifies the request or response based on your criteria.\n\n\`\`\`tcl\n# Your code snippet\nwhen HTTP_REQUEST {\n  # Logic here...\n}\n\`\`\`\n\nI can formatted this for you. If you have specific questions about lines 2-3, let me know!`;

        // Mock specific detection for the user's specific query if possible, or keep it generic but helpful.
        if (text.includes('http_response') && text.includes('http::collect')) {
            response = `**iRule Analysis: Content Replacement**\n\nThis iRule intercepts the server response to replace content in the payload.\n\n\`\`\`tcl\nwhen HTTP_RESPONSE {\n   if {[HTTP::status] == 205} {\n      HTTP::collect [HTTP::header Content-Length]\n      set clen [HTTP::header Content-Length]\n   }\n}\n\nwhen HTTP_RESPONSE_DATA {\n   HTTP::respond 200 content [HTTP::payload]\n   regsub -all "oursite" [HTTP::payload] "oursitedev" newdata\n   log "Replacing payload..."\n   HTTP::payload replace 0 $clen $newdata\n   HTTP::release\n}\n\`\`\`\n\n**Key Actions**:\n1. **Capture**: Collects payload if Status is 205.\n2. **Modify**: Replaces "oursite" with "oursitedev".\n3. **Release**: Sends modified content to client.`;
        }

    } else if (text.includes('when http_request') || text.includes('when client_accepted')) {
        response = "**Analysis of your iRule Code**:\n\n* **Purpose**: This rule intercepts traffic at the HTTP or TCP connection level.\n* **Security**: No obvious vulnerabilities detected in this snippet, but ensure you handle default cases in `switch` statements.\n* **Performance**: Looks efficient.\n\nWould you like me to add comments to this code?";
    } else if (text.includes('improve') || text.includes('optimize')) {
        response = "To improve this iRule, consider:\n- Using `string map` instead of `regsub` for simple replacements.\n- Using `switch -glob` instead of multiple `if/elseif` blocks for better readability and performance.\n- Adding a `default` catch-all to prevent unhandled traffic drops.";
    }

    return {
        role: 'assistant',
        content: response,
        timestamp: Date.now()
    };
}
