import { config } from '../config.ts';
import { startClientBot } from './client.ts';
import { startMasterBot } from './master.ts';

export function startBots(): void {
  if (config.disableBots) {
    console.log('[bots] disabled by DISABLE_BOTS=1');
    return;
  }
  startClientBot();
  startMasterBot();
}
