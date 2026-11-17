#include "geometry.hpp"

#include <stdexcept>

namespace geometry {

Grid::Grid(std::size_t side) : side_(side), cells_(side * side, 0)
{
  if (side == 0 || side > kMaxSide)
  {
    throw std::invalid_argument("side out of range");
  }
}

int Grid::value(Point p) const
{
  const auto index = static_cast<std::size_t>(p.y) * side_
    + static_cast<std::size_t>(p.x);
  return cells_.at(index);
}

void Grid::set(Point p, int v)
{
  const auto index = static_cast<std::size_t>(p.y) * side_
    + static_cast<std::size_t>(p.x);
  cells_.at(index) = v;
}

std::string describe(const Grid& grid, std::string_view name)
{
  std::string out(name);
  out += " grid side ";
  out += std::to_string(grid.side());
  return out;
}

}  // namespace geometry
