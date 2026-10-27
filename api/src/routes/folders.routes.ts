import { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { idParamSchema, objectIdSchema } from '../utils/validation.js';

const createFolderSchema = z.object({
  name: z.string().min(1).max(100),
  parentId: objectIdSchema.optional().nullable(),
  color: z.string().optional().nullable(),
  icon: z.string().optional().nullable(),
});

const updateFolderSchema = z.object({
  name: z.string().min(1).max(100).optional(),
  parentId: objectIdSchema.optional().nullable(),
  color: z.string().optional().nullable(),
  icon: z.string().optional().nullable(),
});

const deleteFolderQuerySchema = z.object({
  deleteContent: z.enum(['true', 'false']).optional(),
});

export async function foldersRoutes(fastify: FastifyInstance) {
  const { folderService } = fastify.container;

  // GET /api/folders
  fastify.get('/', {
    preHandler: [fastify.authenticate],
  }, async (req) => {
    const folders = await folderService.list(req.user.userId);
    return { folders };
  });

  // GET /api/folders/:id
  fastify.get<{
    Params: z.infer<typeof idParamSchema>;
  }>('/:id', {
    preHandler: [fastify.authenticate],
  }, async (req) => {
    const { id } = idParamSchema.parse(req.params);
    return folderService.getById(req.user.userId, id);
  });

  // POST /api/folders
  fastify.post<{
    Body: z.infer<typeof createFolderSchema>;
  }>('/', {
    preHandler: [fastify.authenticate],
  }, async (req, reply) => {
    const input = createFolderSchema.parse(req.body);
    const folder = await folderService.create({
      userId: req.user.userId,
      ...input,
    });
    return reply.status(201).send(folder);
  });

  // PATCH /api/folders/:id
  fastify.patch<{
    Params: z.infer<typeof idParamSchema>;
    Body: z.infer<typeof updateFolderSchema>;
  }>('/:id', {
    preHandler: [fastify.authenticate, fastify.requireNotDemo],
  }, async (req) => {
    const { id } = idParamSchema.parse(req.params);
    const input = updateFolderSchema.parse(req.body);
    return folderService.update(req.user.userId, id, input);
  });

  // DELETE /api/folders/:id
  // Query param: deleteContent=true to delete all content, otherwise moves content to root
  fastify.delete<{
    Params: z.infer<typeof idParamSchema>;
    Querystring: z.infer<typeof deleteFolderQuerySchema>;
  }>('/:id', {
    preHandler: [fastify.authenticate, fastify.requireNotDemo],
  }, async (req, reply) => {
    const { id } = idParamSchema.parse(req.params);
    const { deleteContent } = deleteFolderQuerySchema.parse(req.query);
    await folderService.delete(req.user.userId, id, deleteContent === 'true');
    return reply.status(204).send();
  });
}
