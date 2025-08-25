import fs from 'node:fs';
import path from 'node:path';

const SECRETS_DIR = path.resolve('.secrets');
const TOKENS_PATH = path.join(SECRETS_DIR, 'figma_tokens.json');

function ensureDir() {
  try {
    if (!fs.existsSync(SECRETS_DIR)) {
      fs.mkdirSync(SECRETS_DIR, { recursive: true });
    }
  } catch (e) {
    console.error('[figma-oauth] failed to ensure .secrets directory');
    throw e;
  }
}

export function getTokens() {
  try {
    ensureDir();
    if (!fs.existsSync(TOKENS_PATH)) return null;
    const raw = fs.readFileSync(TOKENS_PATH, 'utf-8');
    if (!raw.trim()) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    return parsed;
  } catch (e) {
    console.error('[figma-oauth] getTokens error');
    return null;
  }
}

export function setTokens(tokens) {
  try {
    ensureDir();
    const safe = {
      access_token: tokens.access_token,
      refresh_token: tokens.refresh_token,
      expires_at: tokens.expires_at,
      token_type: tokens.token_type ?? 'bearer'
    };
    fs.writeFileSync(TOKENS_PATH, JSON.stringify(safe, null, 2));
  } catch (e) {
    console.error('[figma-oauth] setTokens error');
    throw e;
  }
}

export function clearTokens() {
  try {
    ensureDir();
    if (fs.existsSync(TOKENS_PATH)) fs.unlinkSync(TOKENS_PATH);
  } catch (e) {
    console.error('[figma-oauth] clearTokens error');
  }
}

export function tokensFileExists() {
  try {
    return fs.existsSync(TOKENS_PATH);
  } catch {
    return false;
  }
}

export const paths = { SECRETS_DIR, TOKENS_PATH };
