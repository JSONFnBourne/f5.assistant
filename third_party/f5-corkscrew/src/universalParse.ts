'use strict';

/**
 * Universal TMOS Parser
 * 
 * Recursive parser that converts TMOS configuration text to structured JSON.
 * Ported from tmos-converter with enhancements for hierarchical output.
 * 
 * Key features:
 * - Universal recursive parsing (handles any nesting depth)
 * - iRule-aware bracket matching
 * - Handles edge cases: multiline strings, pseudo-arrays, empty objects
 * - Outputs hierarchical structure: ltm.pool["/Common/pool1"]
 */

import logger from './logger';

export interface ParsedObject {
    [key: string]: any;
}

/**
 * Check if a line is the start of an iRule, GTM rule, or PEM iRule
 */
function isRule(str: string): boolean {
    return str.includes('ltm rule') || str.includes('gtm rule') || str.includes('pem irule');
}

/**
 * Count occurrences of a character in a string
 */
function countChar(str: string, char: string): number {
    return str.split(char).length - 1;
}

/**
 * Get indentation level (number of leading spaces)
 */
function countIndent(line: string): number {
    const match = line.match(/^( *)/);
    return match ? match[1].length : 0;
}

/**
 * Remove one level of indentation from array of lines
 */
function removeIndent(arr: string[]): string[] {
    return arr.map(line => line.replace(/^    /, ''));
}

/**
 * Extract the object name/title from a TMOS object line
 * Example: "ltm pool /Common/web_pool {" → "ltm pool /Common/web_pool"
 */
function getTitle(str: string): string {
    return str.replace(/\s?\{\s?}?$/, '').trim();
}

/**
 * Parse a "key value" string into an object
 */
function strToObj(line: string): Record<string, string> {
    const trimmed = line.trim();
    const split = trimmed.split(' ');
    const key = split.shift() ?? '';
    return { [key]: split.join(' ') };
}

/**
 * Convert pseudo-array to actual array
 * Example: "{ /Common/http /Common/tcp }" → ["/Common/http", "/Common/tcp"]
 */
function objToArr(line: string): string[] {
    const match = line.match(/{\s*([\s\S]*?)\s*}/);
    if (match && match[1]) {
        return match[1].trim().split(/\s+/).filter(Boolean);
    }
    return [];
}

/**
 * Handle multiline quoted strings
 */
function arrToMultilineStr(chunk: string[]): Record<string, string> {
    const joined = chunk.join('\n');
    const match = joined.match(/^(\S+)\s+"([\s\S]*)"$/);
    if (match) {
        return { [match[1]]: match[2] };
    }
    // Fallback: try to parse as key-value
    const firstLine = chunk[0]?.trim() ?? '';
    const key = firstLine.split(' ')[0] ?? '';
    const value = chunk.join('\n').replace(new RegExp(`^${key}\\s*`), '').replace(/^"|"$/g, '');
    return { [key]: value };
}

interface GroupResult {
    group: string[][];
    error: Error | null;
}

/**
 * Group root-level TMOS objects from config lines
 * Handles bracket matching, iRule special cases, and escaped characters
 */
function groupObjects(arr: string[]): GroupResult {
    const group: string[][] = [];
    let error: Error | null = null;
    
    try {
        for (let i = 0; i < arr.length; i += 1) {
            const currentLine = arr[i];
            if (!currentLine) continue;

            // Empty object or pseudo-array on single line
            if (currentLine.includes('{') && currentLine.includes('}') && currentLine[0] !== ' ') {
                group.push([currentLine]);
            } else if (currentLine.trim().endsWith('{') && !currentLine.startsWith(' ') && !currentLine.startsWith('#')) {
                let c = 0;
                let ruleLine = '';
                const ruleFlag = isRule(currentLine);
                if (ruleFlag) {
                    ruleLine = currentLine;
                }

                // Bracket counting for finding object end
                let bracketCount = 1;
                let opening = 1;
                let closing = 0;
                
                while (bracketCount !== 0) {
                    c += 1;
                    const line = arr[i + c];

                    if (!line) {
                        if ((opening !== closing) && ruleFlag) {
                            error = new Error(`iRule parsing error, check the following iRule: ${ruleLine}`);
                        }
                        break;
                    }

                    let subcount = 0;

                    // Don't count brackets in comments or special lines inside iRules
                    if (!((line.trim().startsWith('#') || line.trim().startsWith('set') || 
                           line.trim().startsWith('STREAM')) && ruleFlag)) {
                        
                        // Exclude quoted parts and escaped quotes
                        const updatedline = line.trim().replace(/\\"/g, '').replace(/"[^"]*"/g, '');
                        let previousChar = '';
                        
                        updatedline.split('').forEach((char) => {
                            if (previousChar !== '\\') {
                                if (char === '{') {
                                    subcount += 1;
                                    opening += 1;
                                }
                                if (char === '}') {
                                    subcount -= 1;
                                    closing += 1;
                                }
                            }
                            previousChar = char;
                        });

                        // Abort if we run into the next rule
                        if (isRule(line)) {
                            c -= 1;
                            bracketCount = 0;
                            break;
                        }
                        bracketCount += subcount;
                    }
                }
                group.push(arr.slice(i, i + c + 1));
                i += c;
            }
        }
    } catch (e) {
        error = e as Error;
    }
    return { group, error };
}

/**
 * Recursively parse a grouped TMOS object into JSON
 */
function orchestrate(arr: string[]): Record<string, any> {
    const key = getTitle(arr[0] ?? '');
    
    // Preserve original body for the 'line' property (excluding first/last bracket lines)
    const originalBody = arr.slice(1, -1).join('\n');

    // Remove opening and closing bracket lines
    arr.pop();
    arr.shift();

    let obj: any = {};

    // Edge case: iRules (multiline string, preserve as-is)
    if (isRule(key)) {
        obj = arr.join('\n');
    }
    // Edge case: monitor min X of {...}
    else if (key.includes('monitor min')) {
        const trimmedArr = arr.map((s) => s.trim());
        obj = trimmedArr.join(' ').split(' ');
    }
    // Edge case: skip cli script and cert-order-manager (complex quoting)
    else if (!key.includes('cli script') && !key.includes('sys crypto cert-order-manager')) {
        for (let i = 0; i < arr.length; i += 1) {
            const line = arr[i];
            if (!line) continue;

            // Nested object - RECURSIVE
            if (line.endsWith('{') && arr.length !== 1) {
                let c = 0;
                while (arr[i + c] !== '    }') {
                    c += 1;
                    if ((i + c) >= arr.length) {
                        throw new Error(`Missing or mis-indented '}' for line: '${line}'`);
                    }
                }
                const subObjArr = removeIndent(arr.slice(i, i + c + 1));

                // Coerce unnamed objects into indexed array
                let arrIdx = 0;
                const coerceArr = subObjArr.map((subLine) => {
                    if (subLine === '    {') {
                        const newLine = subLine.replace('{', `${arrIdx} {`);
                        arrIdx += 1;
                        return newLine;
                    }
                    return subLine;
                });

                // Recursion for subObjects
                Object.assign(obj, orchestrate(coerceArr));
                i += c;
            }
            // Empty object: "metadata { }"
            else if (line.split(' ').join('').endsWith('{}')) {
                obj[(line.split('{')[0] ?? '').trim()] = {};
            }
            // Pseudo-array: "vlans { /Common/v1 /Common/v2 }"
            else if (line.includes('{') && line.includes('}') && !line.includes('"')) {
                obj[(line.split('{')[0] ?? '').trim()] = objToArr(line);
            }
            // Single-string property (flag)
            else if ((!line.trim().includes(' ') || line.trim().match(/^"[\s\S]*"$/)) && !line.includes('}')) {
                obj[line.trim()] = '';
            }
            // Regular string property on correct indentation level
            else if (countIndent(line) === 4) {
                // Multiline string handling
                const quoteCount = (line.match(/"/g) ?? []).length;
                if (quoteCount % 2 === 1) {
                    let c = 1;
                    while (arr[i + c] && (arr[i + c]?.match(/"/g) ?? []).length % 2 !== 1) {
                        c += 1;
                    }
                    const chunk = arr.slice(i, i + c + 1);
                    const subObjArr = arrToMultilineStr(chunk);
                    obj = Object.assign(obj, subObjArr);
                    i += c;
                } else {
                    // Standard key-value
                    const tmp = strToObj(line.trim());
                    // GTM external monitor user-defined properties
                    if (key.startsWith('gtm monitor external') && Object.keys(tmp).includes('user-defined')) {
                        if (!obj['user-defined']) obj['user-defined'] = {};
                        const tmpObj = strToObj(tmp['user-defined'] ?? '');
                        obj['user-defined'][Object.keys(tmpObj)[0] ?? ''] = Object.values(tmpObj)[0];
                    } else {
                        obj = Object.assign(obj, tmp);
                    }
                }
            } else {
                logger.debug(`UNRECOGNIZED LINE: '${line}'`);
            }
        }
    }

    // If obj is an object (not a string like iRules), add the original body as 'line'
    if (typeof obj === 'object' && obj !== null) {
        obj._originalBody = originalBody;
    }

    return { [key]: obj };
}

/**
 * Pre-process GTM topology records into standard format
 */
function preprocessTopology(fileArr: string[]): string[] {
    const newFileArr: string[] = [];
    const topologyArr: string[] = [];
    let topologyCount = 0;
    let longestMatchEnabled = false;
    let inTopology = false;

    fileArr.forEach((line) => {
        if (line.includes('topology-longest-match') && line.includes('yes')) {
            longestMatchEnabled = true;
        }
        if (line.startsWith('gtm topology ldns:')) {
            inTopology = true;
            if (topologyArr.length === 0) {
                topologyArr.push('gtm topology /Common/Shared/topology {');
                topologyArr.push('    records {');
            }
            const ldnsIndex = line.indexOf('ldns:');
            const serverIndex = line.indexOf('server:');
            const bracketIndex = line.indexOf('{');
            const ldns = line.slice(ldnsIndex + 5, serverIndex).trim();
            topologyArr.push(`        topology_${topologyCount} {`);
            topologyCount += 1;
            topologyArr.push(`            source ${ldns}`);
            const server = line.slice(serverIndex + 7, bracketIndex).trim();
            topologyArr.push(`            destination ${server}`);
        } else if (inTopology) {
            if (line === '}') {
                inTopology = false;
                topologyArr.push('        }');
            } else {
                topologyArr.push(`        ${line}`);
            }
        } else {
            newFileArr.push(line);
        }
    });

    if (topologyArr.length) {
        topologyArr.push(`        longest-match-enabled ${longestMatchEnabled}`);
        topologyArr.push('    }');
        topologyArr.push('}');
    }

    return newFileArr.concat(topologyArr);
}

/**
 * Process iRule comments outside of iRules
 */
function preprocessComments(fileArr: string[]): string[] {
    let iruleDepth = 0;
    
    return fileArr.map(line => {
        if (iruleDepth === 0) {
            if (line.trim().startsWith('# ')) {
                return line.trim().replace('# ', '#comment# ');
            } else if (isRule(line)) {
                iruleDepth += 1;
            }
        } else if (!line.trim().startsWith('#')) {
            iruleDepth = iruleDepth + countChar(line, '{') - countChar(line, '}');
        }
        return line;
    });
}

/**
 * Convert flat parsed object to hierarchical structure
 * "ltm pool /Common/web_pool" → ltm.pool["/Common/web_pool"]
 */
function flatToHierarchical(flat: Record<string, any>): Record<string, any> {
    const result: Record<string, any> = {};

    for (const [key, value] of Object.entries(flat)) {
        const parts = key.split(' ');
        
        if (parts.length < 2) {
            // Single word key (shouldn't happen often)
            result[key] = value;
            continue;
        }

        const category = parts[0];  // ltm, gtm, sys, net, etc.
        
        // Handle various object path depths
        // "ltm virtual /Common/vs" → ltm.virtual["/Common/vs"]
        // "ltm profile http /Common/http" → ltm.profile.http["/Common/http"]
        // "gtm pool a /Common/pool" → gtm.pool.a["/Common/pool"]
        
        if (!result[category]) result[category] = {};
        
        // Find where the object name starts (starts with /)
        let nameIndex = parts.findIndex(p => p.startsWith('/'));
        
        if (nameIndex === -1) {
            // No object name found, might be a system setting
            // "sys global-settings" → sys["global-settings"]
            const restKey = parts.slice(1).join(' ');
            result[category][restKey] = value;
            continue;
        }

        // Build the path between category and name
        const pathParts = parts.slice(1, nameIndex);
        const objectName = parts.slice(nameIndex).join(' ');
        
        // Navigate/create nested structure
        let current = result[category];
        for (const part of pathParts) {
            if (!current[part]) current[part] = {};
            current = current[part];
        }
        
        // Assign the value with metadata
        if (typeof value === 'string') {
            // For rules and simple string values
            current[objectName] = value;
        } else {
            // For objects, add metadata
            const nameParts = objectName.match(/^(\/[\w\d_\-.]+(?:\/[\w\d_\-.]+)?)\/([\w\d_\-.]+)$/);
            
            // Extract and remove the _originalBody, convert to 'line'
            const { _originalBody, ...restValue } = value;
            const enhanced: any = {
                ...restValue,
                line: _originalBody || ''
            };
            
            if (nameParts) {
                enhanced.partition = nameParts[1].split('/')[1];
                if (nameParts[1].split('/').length > 2) {
                    enhanced.folder = nameParts[1].split('/')[2];
                }
                enhanced.name = nameParts[2];
            }
            
            current[objectName] = enhanced;
        }
    }

    return result;
}

/**
 * Recursively convert _originalBody to line in nested objects
 */
function cleanupOriginalBody(obj: any): any {
    if (typeof obj !== 'object' || obj === null) {
        return obj;
    }
    
    if (Array.isArray(obj)) {
        return obj.map(item => cleanupOriginalBody(item));
    }
    
    const result: any = {};
    for (const [key, value] of Object.entries(obj)) {
        if (key === '_originalBody') {
            result.line = value;
        } else if (typeof value === 'object' && value !== null) {
            result[key] = cleanupOriginalBody(value);
        } else {
            result[key] = value;
        }
    }
    return result;
}

/**
 * Parse TMOS configuration text to hierarchical JSON
 * 
 * @param configText - Raw TMOS configuration text
 * @returns Parsed hierarchical JSON object
 */
export function parseConfig(configText: string): Record<string, any> {
    try {
        // Normalize line endings
        const normalized = configText.replace(/\r\n/g, '\n');
        let fileArr = normalized.split('\n');

        // Pre-process GTM topology
        fileArr = preprocessTopology(fileArr);

        // Pre-process comments
        fileArr = preprocessComments(fileArr);

        // Filter empty lines and found comments
        fileArr = fileArr.filter(line => !(line === '' || line.trim().startsWith('#comment# ')));

        // Group root-level objects
        const groupResult = groupObjects(fileArr);
        if (groupResult.error) {
            logger.error(groupResult.error.message);
            throw groupResult.error;
        }

        // Parse each group
        const flatParsed: Record<string, any> = {};
        for (const group of groupResult.group) {
            const parsed = orchestrate(group);
            Object.assign(flatParsed, parsed);
        }

        // Convert to hierarchical structure
        const hierarchical = flatToHierarchical(flatParsed);
        
        // Clean up nested _originalBody -> line
        return cleanupOriginalBody(hierarchical);

    } catch (e) {
        const err = e as Error;
        if (err.message.startsWith('iRule parsing error')) {
            throw err;
        }
        err.message = `Error parsing configuration: ${err.message}`;
        throw err;
    }
}

export default parseConfig;
