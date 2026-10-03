namespace VrageCage.Core;

public sealed class SeekController : IController
{
    public Control Sample(Scenario scenario, VehicleState vehicle, int tick)
    {
        var desired = Math.Atan2(scenario.Goal.Position.Z - vehicle.Z,
            scenario.Goal.Position.X - vehicle.X);
        var error = Normalize(desired - vehicle.HeadingRadians);
        var steering = Math.Clamp(error / 0.6, -1, 1);
        var throttle = Math.Abs(error) > 1.2 ? 0.15 : 1.0;
        return new Control(throttle, steering);
    }

    private static double Normalize(double angle)
    {
        while (angle > Math.PI) angle -= Math.PI * 2;
        while (angle < -Math.PI) angle += Math.PI * 2;
        return angle;
    }
}
