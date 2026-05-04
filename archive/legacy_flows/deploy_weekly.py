from prefect.deployments import Deployment
from prefect.server.schemas.schedules import CronSchedule
from flows.weekly_plan_flow import weekly_plan_flow


def deploy():
    deployment = Deployment.build_from_flow(
        flow=weekly_plan_flow,
        name="weekly-meal-plan",
        schedule=CronSchedule(cron="0 6 * * 1"),
    )
    deployment.apply()


if __name__ == "__main__":
    deploy()
