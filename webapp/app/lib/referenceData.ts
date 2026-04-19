import { commands } from './data/commands';
import { events } from './data/events';
import { operators } from './data/operators';

export interface ReferenceItem {
  name: string;
  type: 'Command' | 'Event' | 'Operator';
  summary: string;
  url?: string;
}

export const IRULE_COMMANDS = commands;
export const IRULE_EVENTS = events;
export const IRULE_OPERATORS = operators;

const precomputedCommands: ReferenceItem[] = commands.map(c => ({
  name: c.name,
  type: 'Command' as const,
  summary: c.documentation,
  url: c.url
}));

const precomputedEvents: ReferenceItem[] = events.map(e => ({
  name: e.name,
  type: 'Event' as const,
  summary: e.documentation,
  url: e.url
}));

const precomputedOperators: ReferenceItem[] = operators.map(o => ({
  name: o.name,
  type: 'Operator' as const,
  summary: o.documentation
}));

const allSorted = [...precomputedCommands, ...precomputedEvents, ...precomputedOperators].sort((a, b) => a.name.localeCompare(b.name));
const commandsSorted = [...precomputedCommands].sort((a, b) => a.name.localeCompare(b.name));
const eventsSorted = [...precomputedEvents].sort((a, b) => a.name.localeCompare(b.name));
const operatorsSorted = [...precomputedOperators].sort((a, b) => a.name.localeCompare(b.name));

export function getReferenceItems(type?: 'Command' | 'Event' | 'Operator'): ReferenceItem[] {
  if (type === 'Command') return commandsSorted;
  if (type === 'Event') return eventsSorted;
  if (type === 'Operator') return operatorsSorted;
  return allSorted;
}
