import { Router, type IRouter } from "express";
import healthRouter from "./health";
import marketsRouter from "./markets";
import snapshotsRouter from "./snapshots";
import signalsRouter from "./signals";
import strategiesRouter from "./strategies";
import ordersRouter from "./orders";
import configRouter from "./config";
import executionRouter from "./execution";

const router: IRouter = Router();

router.use(healthRouter);
router.use(marketsRouter);
router.use(snapshotsRouter);
router.use(signalsRouter);
router.use(strategiesRouter);
router.use(ordersRouter);
router.use(configRouter);
router.use(executionRouter);

export default router;
