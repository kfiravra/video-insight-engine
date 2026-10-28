import { FastifyInstance } from 'fastify';
import fp from 'fastify-plugin';

/**
 * Reconciles `ADMIN_EMAILS` with the users collection once the app is ready
 * (after the mongodb plugin's own onReady has created the indexes). A failure
 * is logged and never blocks the boot: the API must come up even if the
 * reconcile could not run, and the roles already in the database stay as they are.
 */
async function adminSeedSetup(fastify: FastifyInstance): Promise<void> {
  fastify.addHook('onReady', async () => {
    try {
      await fastify.container.adminSeedService.run();
    } catch (err) {
      fastify.log.error({ err }, 'admin_seed_failed');
    }
  });
}

export const adminSeedPlugin = fp(adminSeedSetup, { name: 'admin-seed-plugin' });
