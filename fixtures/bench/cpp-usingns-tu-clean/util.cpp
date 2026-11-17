#include <string>

using namespace std;

int Clamp(int value, int low, int high)
{
    return value < low ? low : (value > high ? high : value);
}
