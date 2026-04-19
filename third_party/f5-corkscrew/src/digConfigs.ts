
'use strict';

import logger from './logger';
import { BigipConfObj, TmosApp } from './models'
import { RegExTree } from './regex';
import { pathValueFromKey } from './objects';
import { poolsInPolicy } from './pools';


/**
 * scans vs config, and discovers child configs
 * @param vsName virtual server name
 * @param vsConfig virtual server tmos config body 
 */
export async function digVsConfig(vsName: string, vsConfig: BigipConfObj["ltm"]['virtual']['key'], configTree: BigipConfObj, rx: RegExTree) {

    /**
     * 
     * What do we need to map on next on the virtual servers?:
     *  - oneConnect?
     *  - expand the discovery of all profiles (apm and supporting)
     * 
     * Or do we expand the irule references like pools/policies?
     * 
     */

    logger.info(`digging vs config for ${vsName}`);

    // clone the app config
    const tmpObj = JSON.parse(JSON.stringify(vsConfig));

    // move and recrate the original config line
    delete tmpObj.line;
    const originalCfg = `ltm virtual ${vsName} {${vsConfig.line}}`
    tmpObj.lines = [originalCfg];
    // name and partition are already set by the parser from vsConfig
    const appObj = tmpObj as TmosApp;

    if (appObj.pool) {
        // dig pool details
        // just reassign the parsed pool details into the vs
        const body = configTree.ltm?.pool?.[vsConfig.pool];
        if (body) {
            appObj.lines.push(`ltm pool ${appObj.pool} {${body.line}}`);
        }
        // raw copy the pool config
        appObj.pool = JSON.parse(JSON.stringify(configTree.ltm?.pool?.[vsConfig.pool]));
        delete appObj.pool.line;

        if (appObj.pool?.members) {
            // Clean up line properties from members
            delete (appObj.pool.members as any).line;
            Object.keys(appObj.pool.members).forEach(n => {
                const member = (appObj.pool.members as any)[n];
                if (member && typeof member === 'object') {
                    delete member.line;
                }
                // loop through all the pool members and get the node details
                const name = n.split(':')[0];
                const body = configTree.ltm?.node?.[name]
                if (body) {
                    appObj.lines.push(`ltm node ${name} {${body.line}}`);
                }

            })
        }

        if (appObj?.pool?.monitor) {
            // Handle both string (single monitor) and array (multiple monitors or min X of)
            const monitors = Array.isArray(appObj.pool.monitor)
                ? appObj.pool.monitor
                : [appObj.pool.monitor];

            const processedMonitors: any[] = [];

            monitors.forEach(x => {
                if (typeof x !== 'string') {
                    processedMonitors.push(x); // Already processed
                    return;
                }

                const p = pathValueFromKey(configTree.ltm?.monitor, x)
                if (p) {
                    // clone the monitor config
                    const tmpObj = JSON.parse(JSON.stringify(p.value));
                    delete tmpObj.line;
                    processedMonitors.push(tmpObj);

                    appObj.lines.push(`ltm monitor ${p.path} ${p.key} { ${p.value?.line || ''} }`);
                }
            })

            // Always store monitors as array for consistency
            appObj.pool.monitor = processedMonitors;
        }
    }

    if (appObj.profiles) {
        // dig profiles details
        // profiles is now an object with profile names as keys

        // todo: dig profiles deeper => deep parse profiles/settings

        // Get profile names from object keys, excluding 'line'
        const profileNames = typeof appObj.profiles === 'object' && !Array.isArray(appObj.profiles)
            ? Object.keys(appObj.profiles).filter(k => k !== 'line')
            : (Array.isArray(appObj.profiles) ? appObj.profiles : []);

        // Convert profiles to array format for TmosApp compatibility
        appObj.profiles = profileNames as any;

        profileNames.forEach(name => {
            // check the ltm profiles
            const x = pathValueFromKey(configTree.ltm?.profile, name);
            if (x) {
                appObj.lines.push(`ltm profile ${x.path} ${x.key} {${x.value?.line || ''}}`);
            }

            // check apm profiles
            const y = pathValueFromKey(configTree?.apm?.profile?.access, name);
            if (y) {
                appObj.lines.push(`apm profile access ${y.path} ${y.key} {${y.value?.line || ''}}`);
            }

            // check asm profile
            const z = pathValueFromKey(configTree?.asm?.policy, name);
            if (z) {
                appObj.lines.push(`asm policy ${z.path} ${z.key} {${z.value?.line || ''}}`);
            }
        })
    }

    if (appObj.rules) {
        // dig iRule details
        // rules is now an object with rule names as keys

        // todo: dig deeper like digRuleConfigs() in digConfigs.ts.331

        // Get rule names from object keys, excluding 'line'
        const ruleNames = typeof appObj.rules === 'object' && !Array.isArray(appObj.rules)
            ? Object.keys(appObj.rules).filter(k => k !== 'line')
            : (Array.isArray(appObj.rules) ? appObj.rules : []);

        // Convert rules to array format for TmosApp compatibility
        appObj.rules = ruleNames as any;

        ruleNames.forEach(name => {

            const x = pathValueFromKey(configTree.ltm?.rule, name)
            if (x) {
                appObj.lines.push(`ltm rule ${x.key} {${x.value}}`);
            }
        })
    }

    if (appObj.snat) {
        // dig snat details

        // if this snat string is the name of a snat pool, then replace with snatpool details
        //  if not, then its 'automap' or 'none' => nothing to add here
        if (configTree.ltm?.snatpool?.[vsConfig.snat]) {
            const c = JSON.parse(JSON.stringify(configTree.ltm.snatpool[vsConfig.snat]));
            appObj.lines.push(`ltm snatpool ${vsConfig.snat} { ${c.line} }`)
            delete c.line;
            appObj.snat = c;
        }
    }

    if (appObj.policies) {
        // dig policies details
        // policies is now an object with policy names as keys

        // Get policy names from object keys, excluding 'line'
        const policyNames = typeof appObj.policies === 'object' && !Array.isArray(appObj.policies)
            ? Object.keys(appObj.policies).filter(k => k !== 'line')
            : (Array.isArray(appObj.policies) ? appObj.policies : []);

        // Convert policies to array format for TmosApp compatibility
        appObj.policies = policyNames as any;

        policyNames.forEach(name => {

            const x = pathValueFromKey(configTree.ltm?.policy, name)
            if (x) {
                appObj.lines.push(`ltm policy ${x.key} {${x.value?.line || ''}}`);

                // got through each policy and dig references (like pools)
                const pools = poolsInPolicy(x.value?.line || '')

                if (pools) {
                    pools.forEach(pool => {
                        const cfg = configTree.ltm?.pool?.[pool]
                        // if we got here there should be a pool for the reference,
                        // but just in case, we confirm with (if) statement
                        if (cfg) {
                            // push pool config to list
                            logger.debug(`policy [${x.key}], pool found [${cfg.name}]`);
                            appObj.lines.push(`ltm pool ${cfg.name} {${cfg.line || ''}}`)
                        }
                    })
                }
            }
        })
    }

    if (appObj.persist) {
        // dig persistence details
        const x = pathValueFromKey(configTree.ltm?.persistence, appObj.persist)
        if (x) {
            appObj.lines.push(`ltm persistence ${x.path} ${x.key} {${x.value?.line || ''}}`);
        }
    }

    if (appObj['fallback-persistence']) {
        // dig fallback-persistence details
        const x = pathValueFromKey(configTree.ltm?.persistence, appObj['fallback-persistence'])
        if (x) {
            appObj.lines.push(`ltm persistence ${x.path} ${x.key} {${x.value?.line || ''}}`);
        }
    }

    return appObj;

}


/**
 * get hostname from json config tree (if present)
 * @param configObject to search for hostname
 */
export function getHostname(configObject: BigipConfObj): string | undefined {

    if (configObject?.sys?.['global-settings']?.hostname) {
        const hostname = configObject.sys["global-settings"].hostname;
        return hostname;
    }
}


