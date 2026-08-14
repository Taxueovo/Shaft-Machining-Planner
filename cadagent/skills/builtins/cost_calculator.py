"""
================================================

Cost Calculator Skill

Estimates manufacturing costs based on geometric features

================================================
"""

import logging
from typing import List, Dict, Any, Optional


logger = logging.getLogger(__name__)


# ==============================================================================
# Cost Calculator Skill
# ==============================================================================

class CostCalculatorSkill:
    """
    Manufacturing cost calculation skill
    
    Estimates based on CAD geometric features:
    - Material costs
    - Machining time
    - Tool costs
    """
    
    name = "cost_calculator"
    description = "Estimate manufacturing costs based on geometric features"
    
    @property
    def tools(self) -> List[Dict[str, Any]]:
        """Return available tools"""
        return [
            {
                "name": "calculate_material_cost",
                "description": "Calculate material cost",
                "parameters": {
                    "material": {"type": "string", "required": True},
                    "volume": {"type": "number", "required": True},
                }
            },
            {
                "name": "estimate_machining_time",
                "description": "Estimate machining time",
                "parameters": {
                    "features": {"type": "array", "required": True},
                }
            },
            {
                "name": "calculate_total_cost",
                "description": "Calculate total cost",
                "parameters": {
                    "features": {"type": "array", "required": True},
                    "material": {"type": "string", "required": True},
                }
            },
        ]
    
    def execute(self, tool_name: str, params: Dict[str, Any]) -> Any:
        """Execute cost calculation"""
        logger.info(f"Executing {tool_name} with params: {params}")
        
        if tool_name == "calculate_material_cost":
            return self._calculate_material(
                params.get("material", "steel"),
                params.get("volume", 0)
            )
        elif tool_name == "estimate_machining_time":
            return self._estimate_time(params.get("features", []))
        elif tool_name == "calculate_total_cost":
            return self._calculate_total(
                params.get("features", []),
                params.get("material", "steel")
            )
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
    
    def _calculate_material(self, material: str, volume: float) -> Dict:
        """Calculate material cost"""
        prices = {"steel": 8.0, "aluminum": 25.0, "copper": 50.0}
        price_per_cm3 = prices.get(material.lower(), 10.0)
        return {
            "material": material,
            "volume": volume,
            "cost": volume * price_per_cm3,
            "unit": "CNY"
        }
    
    def _estimate_time(self, features: List[Dict]) -> Dict:
        """Estimate machining time"""
        base_time = 10  # minutes
        for feature in features:
            base_time += 5
        return {
            "features_count": len(features),
            "estimated_time_minutes": base_time,
        }
    
    def _calculate_total(self, features: List[Dict], material: str) -> Dict:
        """Calculate total cost"""
        material_cost = self._calculate_material(material, 100)
        machining = self._estimate_time(features)
        
        hourly_rate = 50  # CNY/hour
        
        return {
            "material_cost": material_cost["cost"],
            "machining_time": machining["estimated_time_minutes"],
            "machining_cost": machining["estimated_time_minutes"] / 60 * hourly_rate,
            "total_cost": material_cost["cost"] + (machining["estimated_time_minutes"] / 60 * hourly_rate),
            "currency": "CNY"
        }


# ==============================================================================
# Module Interface
# ==============================================================================

def get_instance() -> CostCalculatorSkill:
    """Get singleton instance of the skill"""
    global _instance
    if _instance is None:
        _instance = CostCalculatorSkill()
    return _instance


_instance: Optional[CostCalculatorSkill] = None