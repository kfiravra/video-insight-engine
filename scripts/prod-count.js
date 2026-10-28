// Read-only count of what the billing migration will touch (billing-roles P3.8).
// Run it on the box BEFORE the migration (docs/DEPLOY.md "Migrate to the minutes
// ledger"); it prints, it never writes: only find / aggregate / countDocuments.
//
//   docker exec -i vie-mongodb sh -c 'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" \
//     -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin \
//     video-insight-engine --eval "$(cat)"' < scripts/prod-count.js
//
// Prints: users by tier, admins (count, role/roleSource breakdown, emails masked),
// today's userCostAdjustments with the minutes the migration would turn them into,
// and how many recent llm_usage rows carry a user_id.

const DB_NAME = 'video-insight-engine';
const USAGE_DAYS = 7; // llm_usage window, counted back from now
const MINUTES_PER_USD = 30; // Q9: the same rate the migration uses

const d = db.getSiblingDB(DB_NAME);
const today = new Date().toISOString().slice(0, 10);

function section(title) {
  print('\n== ' + title);
}

// k***@gmail.com: enough to spot a typo, never the address.
function maskEmail(email) {
  if (typeof email !== 'string') return '(no email)';
  const at = email.indexOf('@');
  return at <= 0 ? '***' : email[0] + '***' + email.slice(at);
}

function usersByTier() {
  section('Users by tier');
  print('total: ' + d.users.countDocuments({}));
  print('soft-deleted (skipped by the migration): ' + d.users.countDocuments({ deletedAt: { $ne: null } }));
  const rows = d.users
    .aggregate([
      { $group: { _id: { $ifNull: ['$tier', '(none = free)'] }, users: { $sum: 1 } } },
      { $sort: { users: -1 } },
    ])
    .toArray();
  rows.forEach((row) => print('  ' + row._id + ': ' + row.users));
}

function adminKind(user) {
  if (!Array.isArray(user.roles)) return 'legacy role only (no roles array)';
  return 'roles ' + JSON.stringify(user.roles) + ', roleSource ' + (user.roleSource || '(none)');
}

function admins() {
  section('Admins (effective: roles has admin, or legacy role admin with no roles array)');
  const filter = { $or: [{ roles: 'admin' }, { roles: { $exists: false }, role: 'admin' }] };
  const found = d.users.find(filter, { email: 1, role: 1, roles: 1, roleSource: 1, deletedAt: 1 }).toArray();
  print('count: ' + found.length);
  print('with the legacy role field (what the migration demotes or converts): ' + d.users.countDocuments({ role: 'admin' }));
  const kinds = {};
  found.forEach((user) => {
    kinds[adminKind(user)] = (kinds[adminKind(user)] || 0) + 1;
  });
  Object.keys(kinds).forEach((kind) => print('  ' + kind + ': ' + kinds[kind]));
  found.forEach((user) => print('  ' + maskEmail(user.email) + (user.deletedAt ? ' (soft-deleted)' : '')));
}

function adjustmentsToday() {
  section('userCostAdjustments dated today (' + today + ' UTC)');
  print('all time: ' + d.userCostAdjustments.countDocuments({}));
  const perUser = d.userCostAdjustments
    .aggregate([
      { $match: { date: today } },
      { $group: { _id: '$userId', rows: { $sum: 1 }, amountUsd: { $sum: '$amountUsd' } } },
    ])
    .toArray();
  print('today: ' + perUser.reduce((sum, row) => sum + row.rows, 0) + ' rows for ' + perUser.length + ' users');
  perUser.forEach((row) => {
    const netCredit = -row.amountUsd; // a grant is stored negative
    const spend = (d.userCosts.findOne({ userId: row._id, date: today }) || {}).totalCostUsd || 0;
    const minutes = Math.floor(Math.max(0, netCredit - Math.max(0, spend)) * MINUTES_PER_USD + 1e-6);
    print('  user ' + row._id + ': net credit $' + netCredit.toFixed(2) + ', spent $' + spend.toFixed(2) + ' -> ' + minutes + ' minutes');
  });
}

// Missing and null both count as "without"; only a non-empty string is a user id.
const HAS_USER_ID = {
  $and: [{ $eq: [{ $type: '$user_id' }, 'string'] }, { $ne: ['$user_id', ''] }],
};

function llmUsageCoverage() {
  section('llm_usage.user_id coverage, last ' + USAGE_DAYS + ' days');
  const since = new Date(Date.now() - USAGE_DAYS * 24 * 60 * 60 * 1000);
  const rows = d.llm_usage
    .aggregate([
      { $match: { timestamp: { $gte: since } } },
      {
        $group: {
          _id: { day: { $dateToString: { format: '%Y-%m-%d', date: '$timestamp' } }, hasUserId: HAS_USER_ID },
          rows: { $sum: 1 },
        },
      },
      { $sort: { '_id.day': 1 } },
    ])
    .toArray();
  const total = { with: 0, without: 0 };
  rows.forEach((row) => {
    total[row._id.hasUserId ? 'with' : 'without'] += row.rows;
    print('  ' + row._id.day + (row._id.hasUserId ? '  with user_id:    ' : '  without user_id: ') + row.rows);
  });
  const all = total.with + total.without;
  print('with user_id: ' + total.with + ', without: ' + total.without + (all > 0 ? ' (' + Math.round((100 * total.with) / all) + '% attributed)' : ''));
}

print('Billing migration count, ' + new Date().toISOString() + ', database ' + DB_NAME + ' (read-only)');
usersByTier();
admins();
adjustmentsToday();
llmUsageCoverage();
print('\nNothing was written.');
