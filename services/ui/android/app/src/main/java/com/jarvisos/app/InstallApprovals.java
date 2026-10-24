package com.jarvisos.app;

import java.util.Collection;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * Install approvals (Phase 11 step 5, Family Link's "ask to install"): an app
 * installed on a child's phone after the guard first ran stays closed until a
 * parent says yes. Apps already on the phone then (the baseline) are left as
 * they were; Jarvis itself is never held.
 *
 * Plain Java, JVM-tested. The parents' answers come from the server's status
 * ({@code app_approvals}: package to pending, approved or denied).
 */
public final class InstallApprovals {

    public static final String APPROVED = "approved";
    public static final String DENIED = "denied";

    private InstallApprovals() {}

    /** Apps that are new since the baseline and not approved: closed while that lasts. */
    public static Set<String> awaiting(boolean required, Collection<String> installed,
                                       Collection<String> baseline, Map<String, String> approvals) {
        Set<String> out = new TreeSet<>();
        if (!required || installed == null) return out;
        for (String pkg : installed) {
            if (pkg == null || pkg.isEmpty() || GuardRules.OWN_PACKAGE.equals(pkg)) continue;
            if (baseline != null && baseline.contains(pkg)) continue;
            if (approvals != null && APPROVED.equals(approvals.get(pkg))) continue;
            out.add(pkg);
        }
        return out;
    }

    /** Of the waiting apps, those the parents have not been asked about yet. */
    public static Set<String> toAsk(Collection<String> awaiting, Map<String, String> approvals) {
        Set<String> out = new TreeSet<>();
        if (awaiting == null) return out;
        for (String pkg : awaiting) {
            if (approvals == null || !approvals.containsKey(pkg)) out.add(pkg);
        }
        return out;
    }

    /** Whether a parent already said no (the screen says so instead of "waiting"). */
    public static boolean denied(String pkg, Map<String, String> approvals) {
        return approvals != null && DENIED.equals(approvals.get(pkg));
    }
}
