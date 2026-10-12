const forbiddenCredentialName = /(research|supabase).*service[_-]?role|service[_-]?role.*(research|supabase)/i;

export function assertSafeDashboardEnvironment(environment: Record<string, string | undefined>): void {
  for (const name of ["NEXT_PUBLIC_SUPABASE_ANON_KEY", "NEXT_PUBLIC_RESEARCH_SUPABASE_ANON_KEY"]) {
    const value = environment[name];
    if (value && isServiceRoleCredential(value)) {
      throw new Error(`${name} contains a service-role credential; only an anonymous key is allowed.`);
    }
  }
  for (const [name, value] of Object.entries(environment)) {
    if (value && forbiddenCredentialName.test(name)) {
      throw new Error(`Dashboard configuration refuses service-role credential variable ${name}.`);
    }
    if (
      value
      && (/^NEXT_PUBLIC_/i.test(name) || /(research|supabase)/i.test(name))
      && isServiceRoleCredential(value)
    ) {
      throw new Error(`Dashboard configuration refuses a service-role credential value in ${name}.`);
    }
  }
}

export function isServiceRoleCredential(value: string): boolean {
  if (/service[_-]?role/i.test(value)) return true;
  const segments = value.split(".");
  if (segments.length !== 3) return false;
  try {
    const payload = JSON.parse(decodeBase64Url(segments[1])) as { role?: unknown };
    return payload.role === "service_role";
  } catch {
    return false;
  }
}

function decodeBase64Url(value: string): string {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
  if (typeof atob === "function") {
    return atob(padded);
  }
  return Buffer.from(padded, "base64").toString("utf8");
}
